"""Replying inside an existing thread.

The bug this pins: mail_send had no way to say "this answers that message", so
every reply left as a new conversation carrying nothing but a "Re:" subject.
Gmail often guessed it back into the thread; Outlook did not, and nobody could
tell from the tool's own answer which had happened.

Three layers, tested where each one actually decides something:
* ``mime`` -- the headers that do the threading, and the chain trim.
* ``_common`` -- the subject/address derivation the tool layer does.
* the tools, through FastMCP -- that ``reply_to_uid`` reaches the provider and
  that the derived recipients and subject are what goes out.
"""

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.soverin.mime import (
    MAX_REFERENCES,
    build_email,
    parse_references,
    reply_chain,
)
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_tools
from squirrel_mcp.tools._common import bare_addresses, reply_subject

PARENT = "<CABZfRwFBC1QPc3gd0R@mail.gmail.com>"


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


# ---- the headers ---------------------------------------------------------- #


def test_build_email_carries_the_threading_headers():
    msg = build_email(
        "me@example.com", ["a@b.com"], "Re: Hello", "body",
        in_reply_to=PARENT, references=["<root@x>", PARENT],
    )
    assert msg["In-Reply-To"] == PARENT
    assert msg["References"] == f"<root@x> {PARENT}"


def test_a_plain_send_carries_no_threading_headers():
    """Absence matters as much as presence: an unrelated message must not
    claim to answer anything."""
    msg = build_email("me@example.com", ["a@b.com"], "Hello", "body")
    assert msg["In-Reply-To"] is None and msg["References"] is None


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<a@x> <b@x>", ["<a@x>", "<b@x>"]),
        # Outlook writes them comma-separated, and long headers arrive folded.
        ("<a@x>,<b@x>", ["<a@x>", "<b@x>"]),
        ("<a@x>\r\n\t<b@x>", ["<a@x>", "<b@x>"]),
        ("<a@x> <a@x>", ["<a@x>"]),
        # Not shaped like a message-id -> dropped rather than guessed at.
        ("garbage <a@x>", ["<a@x>"]),
        ("", []),
        (None, []),
    ],
)
def test_parse_references_reads_what_servers_actually_send(raw, expected):
    assert parse_references(raw) == expected


def test_reply_chain_appends_the_parent():
    assert reply_chain(PARENT, ["<root@x>"]) == ["<root@x>", PARENT]


def test_reply_chain_without_a_parent_id_keeps_the_ancestors():
    assert reply_chain(None, ["<root@x>"]) == ["<root@x>"]


def test_reply_chain_trims_a_long_thread_keeping_root_and_recent():
    ancestors = [f"<{i}@x>" for i in range(50)]
    chain = reply_chain(PARENT, ancestors)
    assert len(chain) == MAX_REFERENCES
    # The root ties the conversation together; the tail is what clients match on.
    assert chain[0] == "<0@x>"
    assert chain[-1] == PARENT
    assert chain[-2] == "<49@x>"


# ---- the derivation ------------------------------------------------------- #


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("Hello", "Re: Hello"),
        # A prefix must not accumulate per hop -- Outlook threads on the
        # subject as a fallback and "Re: Re: x" is a different string.
        ("Re: Hello", "Re: Hello"),
        ("RE: Hello", "RE: Hello"),
        ("re:Hello", "re:Hello"),
        ("AW: Hallo", "AW: Hallo"),
        ("Re[2]: Hello", "Re[2]: Hello"),
        ("", "Re:"),
    ],
)
def test_reply_subject(subject, expected):
    assert reply_subject(subject) == expected


def test_bare_addresses_unwraps_display_names():
    """A display name is fine in a To header and fatal in an SMTP envelope."""
    assert bare_addresses(["Anna <anna@x.eu>", "bob@x.eu"]) == ["anna@x.eu", "bob@x.eu"]


def test_bare_addresses_deduplicates_case_insensitively():
    assert bare_addresses(["Anna <anna@x.eu>", "ANNA@x.eu"]) == ["anna@x.eu"]


# ---- the tools ------------------------------------------------------------ #


async def test_send_forwards_the_reply_target_to_the_provider(app_with_tools, fake_provider):
    """The tool layer does not build headers -- it tells the backend which
    message is being answered and lets it thread its own way."""
    await app_with_tools.call_tool(
        "mail_send",
        {"body": "sure", "reply_to_uid": "101", "reply_to_folder": "INBOX", "confirm": True},
    )
    assert fake_provider.send_kwargs[-1]["reply_to_uid"] == "101"
    assert fake_provider.send_kwargs[-1]["reply_to_folder"] == "INBOX"


async def test_reply_derives_recipient_and_subject_from_the_original(
    app_with_tools, fake_provider
):
    result = await app_with_tools.call_tool(
        "mail_send", {"body": "sure", "reply_to_uid": "101", "confirm": True}
    )
    to, subject, _body = fake_provider.sent[-1]
    assert to == ["anna@example.com"]  # the display name is unwrapped
    assert subject == "Re: Hello"
    # And the client is told it answered a thread, by message-id.
    assert "<abc@example.com>" in str(result)


async def test_reply_honours_reply_to_over_from(app_with_tools, fake_provider):
    """A sender who set Reply-To asked for answers somewhere else; ignoring it
    sends a mailing-list reply to the wrong place."""
    fake_provider.reply_to_addrs = ["list@example.com"]
    await app_with_tools.call_tool(
        "mail_send", {"body": "sure", "reply_to_uid": "101", "confirm": True}
    )
    assert fake_provider.sent[-1][0] == ["list@example.com"]


async def test_explicit_recipient_and_subject_win_over_the_original(
    app_with_tools, fake_provider
):
    await app_with_tools.call_tool(
        "mail_send",
        {
            "to": "someone@else.com",
            "subject": "My own subject",
            "body": "sure",
            "reply_to_uid": "101",
            "confirm": True,
        },
    )
    to, subject, _ = fake_provider.sent[-1]
    assert to == ["someone@else.com"] and subject == "My own subject"


async def test_reply_all_cc_s_the_other_participants_but_never_me(
    app_with_tools, fake_provider
):
    await app_with_tools.call_tool(
        "mail_send",
        {"body": "sure", "reply_to_uid": "101", "reply_all": True, "confirm": True},
    )
    cc = fake_provider.send_kwargs[-1]["cc"]
    assert "bob@example.com" in cc and "carol@example.com" in cc
    # The mailbox doing the replying was on the original's To line; cc'ing
    # yourself on every reply is the classic reply-all bug.
    assert "me@example.com" not in cc


async def test_reply_all_does_not_duplicate_the_direct_recipient(
    app_with_tools, fake_provider
):
    fake_provider.reply_to_addrs = ["bob@example.com"]
    await app_with_tools.call_tool(
        "mail_send",
        {"body": "sure", "reply_to_uid": "101", "reply_all": True, "confirm": True},
    )
    to = fake_provider.sent[-1][0]
    cc = fake_provider.send_kwargs[-1]["cc"]
    assert to == ["bob@example.com"] and "bob@example.com" not in cc


async def test_a_reply_still_needs_confirmation(app_with_tools, fake_provider):
    with pytest.raises(Exception) as exc:
        await app_with_tools.call_tool("mail_send", {"body": "sure", "reply_to_uid": "101"})
    assert "confirm" in str(exc.value).lower()
    assert fake_provider.sent == []


async def test_a_non_reply_send_still_demands_to_and_subject(app_with_tools, fake_provider):
    """Making them optional is only true when replying -- otherwise the tool
    must still refuse rather than send a blank-subject mail to nobody."""
    with pytest.raises(Exception) as exc:
        await app_with_tools.call_tool("mail_send", {"body": "yo", "confirm": True})
    assert "to" in str(exc.value).lower()
    assert fake_provider.sent == []

    with pytest.raises(Exception) as exc:
        await app_with_tools.call_tool(
            "mail_send", {"to": "x@y.com", "body": "yo", "confirm": True}
        )
    assert "subject" in str(exc.value).lower()
    assert fake_provider.sent == []


async def test_send_still_demands_a_body(app_with_tools, fake_provider):
    with pytest.raises(Exception) as exc:
        await app_with_tools.call_tool(
            "mail_send", {"to": "x@y.com", "subject": "hi", "confirm": True}
        )
    assert "body" in str(exc.value).lower()
    assert fake_provider.sent == []


async def test_draft_can_be_a_reply_too(app_with_tools, fake_provider):
    """Drafting a reply for the user to review is the recommended path, so it
    has to thread just as well as sending one."""
    result = await app_with_tools.call_tool(
        "mail_draft", {"body": "sure", "reply_to_uid": "101"}
    )
    to, subject, _body, _folder = fake_provider.drafts[-1]
    assert to == ["anna@example.com"] and subject == "Re: Hello"
    assert fake_provider.draft_kwargs[-1]["reply_to_uid"] == "101"
    assert "<abc@example.com>" in str(result)


async def test_a_plain_send_reports_no_thread(app_with_tools, fake_provider):
    """`in_reply_to` is how a client knows which of the two happened."""
    result = await app_with_tools.call_tool(
        "mail_send", {"to": "x@y.com", "subject": "hi", "body": "yo", "confirm": True}
    )
    assert fake_provider.send_kwargs[-1]["reply_to_uid"] is None
    assert "'in_reply_to': None" in str(result)


async def test_reply_arguments_are_in_the_tool_schema(app_with_tools):
    """A client can only reply if the schema tells it how."""
    tools = {t.name: t for t in await app_with_tools.list_tools()}
    for name in ("mail_send", "mail_draft"):
        props = tools[name].inputSchema.get("properties", {})
        assert {"reply_to_uid", "reply_to_folder", "reply_all"} <= set(props), name


# ---- the IMAP/SMTP provider's own seam ------------------------------------ #
#
# The headers live on the IMAP side and the message that needs them goes out
# over SMTP; SoverinMailProvider is the only thing that joins the two, so a
# missing hand-off there sends a perfectly valid unthreaded mail.


def _soverin_provider(monkeypatch):
    from squirrel_mcp.providers.soverin.provider import SoverinMailProvider

    provider = SoverinMailProvider(
        SquirrelConfig(
            mail_email="me@example.com",
            mail_password="pw",
            imap_host="imap.x.eu",
            smtp_host="smtp.x.eu",
        )
    )
    sent: dict = {}
    monkeypatch.setattr(
        provider._imap, "reply_headers", lambda folder, uid: (PARENT, ["<root@x>", PARENT])
    )
    monkeypatch.setattr(
        provider._smtp,
        "send",
        lambda *a, **kw: sent.update(args=a, kwargs=kw)
        or {"message_id": "<new@x>", "recipients": list(a[0])},
    )
    return provider, sent


def test_provider_reads_the_headers_over_imap_and_sends_them_over_smtp(monkeypatch):
    provider, sent = _soverin_provider(monkeypatch)
    provider.send(["anna@example.com"], "Re: Hello", "sure", reply_to_uid="101")
    assert sent["kwargs"]["in_reply_to"] == PARENT
    assert sent["kwargs"]["references"] == ["<root@x>", PARENT]


def test_provider_sends_an_ordinary_message_with_no_headers_at_all(monkeypatch):
    provider, sent = _soverin_provider(monkeypatch)
    provider.send(["anna@example.com"], "Hello", "hi")
    assert sent["kwargs"]["in_reply_to"] is None
    assert sent["kwargs"]["references"] is None
