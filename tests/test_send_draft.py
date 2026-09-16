"""Sending a draft that already exists.

The gap this closes: Squirrel could write a draft and edit it, but not send
it. The only way out was to re-type the draft's text into ``mail_send``, which
puts a *second* message on the wire and leaves the reviewed one sitting in
Drafts -- the duplicate the user finds afterwards.

The decision that makes it correct is that the draft is sent **as it stands**.
A draft is already a complete RFC 5322 message; rebuilding one from the fields
the tools model would drop everything they do not -- the ``multipart/related``
an embedded image lives in, the ``In-Reply-To`` that makes it a reply, a header
another mail client wrote. So the tool takes a uid and nothing else, and the
bytes on the wire are the bytes that were reviewed. Two headers are the
exception and are tested here: ``Date``, which would otherwise say when the
draft was written, and a missing ``Message-ID``.

The rest is the send path's existing contract read for a second caller: the
Bcc header stays on the Sent copy and comes off what leaves, the Sent copy is
never allowed to fail the send -- and neither is removing the draft, for
exactly the same reason.
"""

from __future__ import annotations

import email
import email.utils
from email.message import EmailMessage

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import MailProviderError
from squirrel_mcp.providers.soverin.provider import SoverinMailProvider
from squirrel_mcp.providers.soverin.smtp import SoverinSmtpClient
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_tools


@pytest.fixture
def app_with_tools(fake_provider):
    app = create_fastmcp_app()
    cfg = SquirrelConfig(
        mail_email="me@example.com",
        mail_password="pw",
        imap_host="imap.x.eu",
        smtp_host="smtp.x.eu",
    )
    register_tools(app, fake_provider, cfg)
    return app


def _draft(*, bcc: str = "", message_id: str = "<draft@example.com>") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = "me@example.net"
    msg["To"] = "Anna <anna@example.com>, bob@example.com"
    msg["Cc"] = "carol@example.com"
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = "Reviewed and ready"
    if message_id:
        msg["Message-ID"] = message_id
    msg["Date"] = email.utils.formatdate(0, localtime=False)  # 1970
    msg.set_content("The body the user approved.")
    return msg


class FakeSmtp(SoverinSmtpClient):
    """Everything up to the socket, and nothing past it."""

    def __init__(self):
        super().__init__("smtp.example.net", 465, "me@example.net", "pw", "me@example.net")
        self.delivered: list = []

    def _deliver(self, msg, recipients, size_bytes):
        self.delivered.append((msg.as_bytes(), list(recipients)))


# ---- SMTP: what actually leaves ------------------------------------------- #
def test_recipients_come_from_the_drafts_own_headers():
    """There are no ``to``/``cc`` arguments on this path -- the draft is the
    only statement of who it is for, Bcc included."""
    smtp = FakeSmtp()
    result = smtp.send_existing(_draft(bcc="dan@example.com"))
    assert result["recipients"] == [
        "anna@example.com",
        "bob@example.com",
        "carol@example.com",
        "dan@example.com",
    ]


def test_the_delivered_bytes_are_the_draft():
    """The whole point: what was reviewed is what goes out. Rebuilding the
    message from re-read fields would be a second, subtly different one."""
    smtp = FakeSmtp()
    smtp.send_existing(_draft())
    sent = email.message_from_bytes(smtp.delivered[-1][0])
    assert sent["Subject"] == "Reviewed and ready"
    assert sent.get_payload(decode=True) == b"The body the user approved.\n"


def test_the_message_id_survives_so_the_sent_copy_matches():
    smtp = FakeSmtp()
    result = smtp.send_existing(_draft())
    assert result["message_id"] == "<draft@example.com>"


def test_a_draft_without_a_message_id_gets_one():
    """Ours always have one; another client's need not. It is what the Sent
    copy is deduplicated on and what a reply to this message threads onto."""
    smtp = FakeSmtp()
    result = smtp.send_existing(_draft(message_id=""))
    assert result["message_id"]
    assert email.message_from_bytes(smtp.delivered[-1][0])["Message-ID"] == result["message_id"]


def test_the_date_is_restamped_to_the_moment_it_was_sent():
    """A draft's Date is when it was *written*. Left alone, a message sent
    this afternoon lands in the recipient's inbox above mail they have already
    read."""
    smtp = FakeSmtp()
    smtp.send_existing(_draft())
    sent = email.message_from_bytes(smtp.delivered[-1][0])
    assert email.utils.parsedate_to_datetime(sent["Date"]).year > 1970


def test_bcc_stays_on_the_sent_copy_and_comes_off_what_leaves():
    """Your own Sent folder is the only record of whom you blind-copied; the
    recipients must not be handed that list. Same rule as ``send``."""
    smtp = FakeSmtp()
    result = smtp.send_existing(_draft(bcc="dan@example.com"))
    assert b"dan@example.com" in result["raw"]  # the copy for Sent
    assert b"Bcc:" not in smtp.delivered[-1][0]
    # ...and dan is still delivered to, over the envelope.
    assert "dan@example.com" in smtp.delivered[-1][1]


def test_a_draft_with_no_recipients_is_refused_by_name():
    msg = EmailMessage()
    msg["Subject"] = "Notes to self"
    msg.set_content("...")
    with pytest.raises(MailProviderError, match="no recipients"):
        FakeSmtp().send_existing(msg)


# ---- provider: send, file the copy, then remove the draft ------------------ #
class FakeImap:
    def __init__(self, msg=None, delete_raises=False):
        self._msg = msg if msg is not None else _draft()
        self.deleted: list = []
        self.appended: list = []
        self._delete_raises = delete_raises

    def fetch_outgoing(self, folder, uid):
        return self._msg, ["contract.pdf"]

    def delete_message(self, folder, uid):
        if self._delete_raises:
            raise OSError("connection reset")
        self.deleted.append((folder, uid))

    def append_sent(self, raw, *, message_id=None, when=None):
        self.appended.append(raw)
        return "Sent", True


def _provider(imap, smtp=None) -> SoverinMailProvider:
    p = SoverinMailProvider.__new__(SoverinMailProvider)
    p._imap = imap
    p._smtp = smtp or FakeSmtp()
    p._drafts_folder = "Drafts"
    p._email = "me@example.net"
    return p


def test_the_draft_is_removed_after_it_goes_out():
    imap = FakeImap()
    result = _provider(imap).send_draft("Drafts", "900")
    assert imap.deleted == [("Drafts", "900")]
    assert result["draft_removed"] is True


def test_the_copy_is_filed_in_sent_before_the_draft_is_dropped():
    """Order matters: the only surviving copy must exist somewhere else before
    this one is deleted."""
    imap = FakeImap()
    result = _provider(imap).send_draft("Drafts", "900")
    assert imap.appended, "nothing was filed in Sent"
    assert result["saved_to_sent"] is True and result["sent_folder"] == "Sent"


def test_a_failed_removal_does_not_fail_the_send():
    """The mail is with the recipient by now. Raising here would report a
    delivered message as undelivered and invite a retry that sends it twice --
    the same rule the Sent copy follows."""
    imap = FakeImap(delete_raises=True)
    result = _provider(imap).send_draft("Drafts", "900")
    assert result["message_id"] == "<draft@example.com>"
    assert result["draft_removed"] is False


def test_the_subject_and_attachments_ride_back_for_the_user():
    result = _provider(FakeImap()).send_draft("Drafts", "900")
    assert result["subject"] == "Reviewed and ready"
    assert result["attachments"] == ["contract.pdf"]


# ---- the tool ------------------------------------------------------------- #
def _payload(result) -> dict:
    return result[1] if isinstance(result, tuple) else result


async def test_it_refuses_without_confirm_and_sends_nothing(app_with_tools, fake_provider):
    with pytest.raises(Exception, match="confirm=true"):
        await app_with_tools.call_tool("mail_send_draft", {"uid": "900"})
    assert fake_provider.sent_drafts == []
    assert "900" in fake_provider.draft_store


async def test_it_sends_the_draft_and_reports_what_happened(app_with_tools, fake_provider):
    result = await app_with_tools.call_tool(
        "mail_send_draft", {"uid": "900", "confirm": True}
    )
    assert fake_provider.sent_drafts == [("Drafts", "900")]
    assert "900" not in fake_provider.draft_store
    payload = _payload(result)
    assert payload["subject"] == "Reviewed"
    assert payload["recipients"] == ["bob@example.com"]
    assert payload["draft_removed"] is True
    assert payload["sent_folder"] == "Sent"


async def test_a_draft_that_survived_its_own_send_is_reported_not_hidden(
    app_with_tools, fake_provider
):
    fake_provider.draft_removal_ok = False
    result = await app_with_tools.call_tool(
        "mail_send_draft", {"uid": "900", "confirm": True}
    )
    payload = _payload(result)
    assert payload["draft_removed"] is False
    assert payload["message_id"]  # it WAS sent -- never send it again


async def test_a_backend_without_the_capability_is_told_what_will_work(
    app_with_tools, fake_provider, monkeypatch
):
    """A backend that quietly did nothing here would leave the user believing
    a reviewed message had been sent. Same refusal shape as attachments."""
    monkeypatch.delattr(type(fake_provider), "send_draft")
    with pytest.raises(Exception, match="cannot send an existing draft"):
        await app_with_tools.call_tool("mail_send_draft", {"uid": "900", "confirm": True})
    assert "900" in fake_provider.draft_store  # untouched
