"""The copy in Sent: found by SPECIAL-USE, filed \\Seen, and never fatal.

SMTP delivers a message and keeps nothing. Most hosts (Soverin among them) file
no copy server-side either, so a mail sent through Squirrel really was gone the
moment it left -- delivered to the recipient, absent from the sender's own Sent
folder, and invisible to ``mail_search``. These pin the three decisions that
fix it:

* the Sent folder is **read from the server's ``\\Sent`` attribute**, not
  guessed from a name that is localized on half the mail hosts in Europe;
* the copy is the **bytes that went out**, ``\\Seen`` and stamped with the send
  time, so it is the message the recipient got rather than a re-rendering of it;
* an APPEND that fails **does not fail the send** -- the mail is already with
  the recipient, and raising here would report a delivered message as undelivered
  and invite a retry that sends it twice.
"""

from __future__ import annotations

import datetime

import pytest

from squirrel_mcp.providers.protocol import FolderInfo, MailProviderError
from squirrel_mcp.providers.soverin.imap import SoverinImapClient
from squirrel_mcp.providers.soverin.provider import SoverinMailProvider


class FakeMailBox:
    """Records the APPEND rather than performing it."""

    def __init__(self, existing_uids=()):
        self.selected = None
        self.appended: list = []
        self.searched: list = []
        self._existing = list(existing_uids)

        manager = self

        class _Folder:
            def set(self, name, readonly=False):
                manager.selected = name

        self.folder = _Folder()

    def uids(self, criteria, charset=None):
        self.searched.append(criteria)
        return list(self._existing)

    def append(self, message, folder="INBOX", dt=None, flag_set=None):
        self.appended.append((message, folder, dt, list(flag_set or [])))
        return "OK", [b"[APPENDUID 1 7] done"]


@pytest.fixture
def client() -> SoverinImapClient:
    c = SoverinImapClient(
        host="imap.example.net",
        port=993,
        username="me@example.net",
        password="pw",
        email="me@example.net",
    )
    c._mailbox = FakeMailBox()
    return c


# ---- finding the folder --------------------------------------------------- #
def test_sent_folder_comes_from_the_special_use_flag(client, monkeypatch):
    """The name is unguessable; the flag is not."""
    monkeypatch.setattr(
        client,
        "list_folders",
        lambda: [
            FolderInfo(name="INBOX", flags=["\\HasNoChildren"]),
            FolderInfo(name="Verzonden items", flags=["\\HasNoChildren", "\\Sent"]),
            FolderInfo(name="Sent", flags=["\\HasNoChildren"]),
        ],
    )
    assert client.sent_folder() == "Verzonden items"


def test_sent_folder_falls_back_to_sent(client, monkeypatch):
    monkeypatch.setattr(
        client,
        "list_folders",
        lambda: [FolderInfo(name="INBOX", flags=["\\HasNoChildren"])],
    )
    assert client.sent_folder() == "Sent"


def test_sent_folder_is_resolved_once(client, monkeypatch):
    calls = []

    def listing():
        calls.append(1)
        return [FolderInfo(name="Sent Items", flags=["\\Sent"])]

    monkeypatch.setattr(client, "list_folders", listing)
    assert client.sent_folder() == "Sent Items"
    assert client.sent_folder() == "Sent Items"
    assert len(calls) == 1


def test_a_failed_listing_is_not_cached(client, monkeypatch):
    """One bad LIST must not condemn the whole session to the fallback."""
    state = {"fail": True}

    def listing():
        if state["fail"]:
            raise MailProviderError("connection reset")
        return [FolderInfo(name="Sent Items", flags=["\\Sent"])]

    monkeypatch.setattr(client, "list_folders", listing)
    assert client.sent_folder() == "Sent"
    state["fail"] = False
    assert client.sent_folder() == "Sent Items"


# ---- filing the copy ------------------------------------------------------ #
def test_append_files_the_bytes_seen_and_dated(client, monkeypatch):
    monkeypatch.setattr(client, "list_folders", lambda: [FolderInfo(name="Sent", flags=["\\Sent"])])
    when = datetime.datetime(2026, 8, 13, 9, 30, tzinfo=datetime.timezone.utc)
    folder, appended = client.append_sent(
        b"From: me\r\n\r\nhi", message_id="<abc@example.net>", when=when
    )

    assert (folder, appended) == ("Sent", True)
    message, target, dt, flags = client._mailbox.appended[0]
    assert message == b"From: me\r\n\r\nhi"  # the message that went out, verbatim
    assert target == "Sent"
    assert dt == when  # INTERNALDATE: the copy sorts by when it was sent
    assert flags == ["\\Seen"]  # mail you wrote yourself is not unread


def test_an_existing_copy_is_not_duplicated(client, monkeypatch):
    """A host that files its own copy gets one message in Sent, not two."""
    monkeypatch.setattr(client, "list_folders", lambda: [FolderInfo(name="Sent", flags=["\\Sent"])])
    client._mailbox = FakeMailBox(existing_uids=["42"])

    folder, appended = client.append_sent(b"raw", message_id="<abc@example.net>")

    assert (folder, appended) == ("Sent", False)
    assert client._mailbox.appended == []
    assert client._mailbox.searched == ['HEADER Message-ID "<abc@example.net>"']


def test_no_message_id_means_no_probe(client, monkeypatch):
    monkeypatch.setattr(client, "list_folders", lambda: [FolderInfo(name="Sent", flags=["\\Sent"])])
    client.append_sent(b"raw")
    assert client._mailbox.searched == []
    assert len(client._mailbox.appended) == 1


# ---- the provider joining the two halves ---------------------------------- #
class _Smtp:
    """Stands in for the SMTP client: hands back what it sent."""

    def __init__(self):
        self.calls: list = []

    def send(self, to, subject, body, **kwargs):
        self.calls.append((to, subject, body, kwargs))
        return {
            "message_id": "<x@example.net>",
            "recipients": list(to),
            "raw": b"raw-bytes",
            "sent_at": datetime.datetime(2026, 8, 13, tzinfo=datetime.timezone.utc),
        }


class _Imap:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.calls: list = []

    def append_sent(self, raw, *, message_id=None, when=None):
        self.calls.append((raw, message_id, when))
        if self.exc:
            raise self.exc
        return "Sent", True

    def reply_headers(self, folder, uid):
        return "<parent@example.net>", ["<parent@example.net>"]


def _provider(imap: _Imap, smtp: _Smtp) -> SoverinMailProvider:
    provider = SoverinMailProvider.__new__(SoverinMailProvider)
    provider._imap = imap
    provider._smtp = smtp
    provider._drafts_folder = "Drafts"
    provider._email = "me@example.net"
    return provider


def test_send_files_a_copy_and_reports_it():
    imap, smtp = _Imap(), _Smtp()
    result = _provider(imap, smtp).send(["you@example.com"], "hi", "there")

    assert result["saved_to_sent"] is True
    assert result["sent_folder"] == "Sent"
    assert imap.calls[0][0] == b"raw-bytes"  # exactly what SMTP delivered
    # The raw message and the timestamp are the provider's business alone.
    assert "raw" not in result and "sent_at" not in result


def test_a_reply_is_filed_like_any_other_message():
    imap, smtp = _Imap(), _Smtp()
    result = _provider(imap, smtp).send(
        ["you@example.com"], "Re: hi", "there", reply_to_uid="17", reply_to_folder="INBOX"
    )

    assert result["saved_to_sent"] is True
    assert imap.calls  # the copy was filed, threading headers and all
    assert smtp.calls[0][3]["in_reply_to"] == "<parent@example.net>"


def test_a_failed_append_does_not_fail_the_send():
    """The mail is already with the recipient; raising would invite a resend."""
    imap, smtp = _Imap(exc=MailProviderError("no such folder")), _Smtp()
    result = _provider(imap, smtp).send(["you@example.com"], "hi", "there")

    assert result["message_id"] == "<x@example.net>"
    assert result["saved_to_sent"] is False
    assert result["sent_folder"] is None


# ---- what the SMTP client hands back -------------------------------------- #
def test_smtp_returns_the_bytes_it_sent_with_the_bcc_kept():
    """The copy in your own Sent folder is the only record of a bcc.

    What leaves the machine must not carry the header -- that is the whole
    point of a blind copy -- but the sender's own record of the message is
    exactly where the address belongs, which is what the draft path stores too.
    """
    from squirrel_mcp.providers.soverin.smtp import SoverinSmtpClient

    client = SoverinSmtpClient(
        host="smtp.example.net", port=465, username="me", password="pw",
        email="me@example.net",
    )
    delivered: list = []
    client._deliver = lambda msg, recipients, size: delivered.append(msg.as_bytes())

    result = client.send(["you@example.com"], "hi", "there", bcc=["quiet@example.com"])

    assert b"quiet@example.com" in result["raw"]
    assert b"quiet@example.com" not in delivered[0]
    assert result["sent_at"].tzinfo is not None  # INTERNALDATE needs a real zone
