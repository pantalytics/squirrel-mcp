"""Sending a file, over IMAP/SMTP.

The gap this closes: attachments could be *read* since v1 and could not be sent
at all -- ``build_email`` took no files and no provider method had a parameter
for one, so "send this along" had no path through the stack.

Four layers, each tested where it decides something:
* ``mime`` -- the MIME the stdlib builds, which is the entire mechanism (SMTP
  and IMAP both just carry the bytes).
* ``tools.mail.attachments`` -- how bytes get named, and everything that must
  be refused before a message goes out.
* the tools, through FastMCP -- that the files reach the provider, and that
  editing a draft does not silently drop them.
* ``smtp`` -- the server's own SIZE limit, asked rather than assumed.
"""

import base64
import smtplib

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import MailProviderError, OutgoingAttachment
from squirrel_mcp.providers.soverin.mime import build_email
from squirrel_mcp.providers.soverin.smtp import SoverinSmtpClient, _timeout_for
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_tools
from squirrel_mcp.tools._common import MAX_OUTGOING_TOTAL_BYTES
from squirrel_mcp.tools.mail.attachments import resolve_attachments

PDF = b"%PDF-1.4 pretend this is a real invoice"


def _att(filename="invoice.pdf", content_type="application/pdf", content=PDF):
    return OutgoingAttachment(filename=filename, content_type=content_type, content=content)


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


# ---- the MIME ------------------------------------------------------------- #


def _parts(msg):
    return {p.get_filename(): p for p in msg.walk() if p.get_filename()}


def test_one_attachment_becomes_a_mixed_part_with_the_bytes_intact():
    msg = build_email("me@x.eu", ["a@b.com"], "Here", "See attached.", attachments=[_att()])
    assert msg.get_content_type() == "multipart/mixed"
    part = _parts(msg)["invoice.pdf"]
    assert part.get_content_type() == "application/pdf"
    # base64 is not decoration: SMTP is 7-bit with a 998-octet line limit.
    assert part["Content-Transfer-Encoding"] == "base64"
    assert part.get_payload(decode=True) == PDF
    assert part.get_content_disposition() == "attachment"


def test_several_attachments_all_arrive_under_one_message():
    files = [_att("a.pdf"), _att("b.txt", "text/plain", b"hello"), _att("c.png", "image/png", b"\x89PNG")]
    msg = build_email("me@x.eu", ["a@b.com"], "Three", "body", attachments=files)
    parts = _parts(msg)
    assert set(parts) == {"a.pdf", "b.txt", "c.png"}
    assert parts["b.txt"].get_payload(decode=True) == b"hello"
    assert parts["c.png"].get_content_type() == "image/png"


def test_a_non_ascii_filename_survives():
    """RFC 2231 encoding is the stdlib's job -- this pins that we let it do it."""
    msg = build_email(
        "me@x.eu", ["a@b.com"], "s", "b", attachments=[_att("jaarrekening-2026-café.pdf")]
    )
    assert "jaarrekening-2026-café.pdf" in _parts(msg)


def test_a_broken_content_type_falls_back_to_opaque_bytes():
    """A part needs both halves of a type; anything else is octet-stream,
    which is what a client does with an unknown type regardless."""
    msg = build_email("me@x.eu", ["a@b.com"], "s", "b", attachments=[_att(content_type="nonsense")])
    assert _parts(msg)["invoice.pdf"].get_content_type() == "application/octet-stream"


def test_without_attachments_the_message_stays_a_plain_single_part():
    """The regression that matters most: every message sent before this
    existed must keep exactly the shape it had."""
    msg = build_email("me@x.eu", ["a@b.com"], "s", "body")
    assert msg.get_content_type() == "text/plain"
    assert not msg.is_multipart()


def test_attachments_do_not_disturb_the_threading_headers():
    msg = build_email(
        "me@x.eu", ["a@b.com"], "Re: s", "b",
        in_reply_to="<parent@x>", references=["<root@x>", "<parent@x>"],
        attachments=[_att()],
    )
    assert msg["In-Reply-To"] == "<parent@x>"
    assert msg["References"] == "<root@x> <parent@x>"


# ---- naming the bytes ----------------------------------------------------- #


async def test_base64_form_decodes_and_keeps_the_filename(fake_provider):
    spec = [{"filename": "notes.txt", "content_base64": base64.b64encode(b"hi").decode()}]
    (att,) = await resolve_attachments(fake_provider, spec)
    assert att.filename == "notes.txt"
    assert att.content == b"hi"
    assert att.content_type == "text/plain"  # guessed from the extension


async def test_an_explicit_content_type_wins_over_the_guess(fake_provider):
    spec = [{"filename": "data.txt", "content_base64": "aGk=", "content_type": "text/csv"}]
    (att,) = await resolve_attachments(fake_provider, spec)
    assert att.content_type == "text/csv"


async def test_an_unguessable_name_falls_back_to_octet_stream(fake_provider):
    spec = [{"filename": "receipt", "content_base64": "aGk="}]
    (att,) = await resolve_attachments(fake_provider, spec)
    assert att.content_type == "application/octet-stream"


async def test_source_form_takes_the_file_straight_off_the_mailbox(fake_provider):
    """Forwarding must not route the bytes through the model: the provider
    fetches the part and hands it back."""
    spec = [{"source_uid": "101", "source_index": 0, "source_folder": "Archive"}]
    (att,) = await resolve_attachments(fake_provider, spec)
    assert att.filename == "doc.pdf"
    assert att.content == b"%PDF-"


async def test_source_form_accepts_a_rename(fake_provider):
    spec = [{"source_uid": "101", "source_index": 0, "filename": "contract-final.pdf"}]
    (att,) = await resolve_attachments(fake_provider, spec)
    assert att.filename == "contract-final.pdf"
    assert att.content == b"%PDF-"


async def test_nothing_asked_for_is_nothing_attached(fake_provider):
    assert await resolve_attachments(fake_provider, None) == []
    assert await resolve_attachments(fake_provider, []) == []


@pytest.mark.parametrize(
    "spec,fragment",
    [
        ([{"filename": "x.pdf"}], "either source_uid"),
        ([{"content_base64": "aGk="}], "filename is required"),
        ([{"filename": "x.pdf", "content_base64": "not base64!!"}], "not valid base64"),
        ([{"filename": "x", "content_base64": "aGk=", "source_uid": "1"}], "pick one"),
        ([{"source_uid": "101", "source_index": "first"}], "must be a number"),
        (["just a string"], "must be an object"),
        ("nonsense", "must be a list"),
    ],
)
async def test_a_malformed_attachment_is_refused_with_a_reason(fake_provider, spec, fragment):
    from squirrel_mcp.error_handling import ValidationError

    with pytest.raises(ValidationError) as e:
        await resolve_attachments(fake_provider, spec)
    assert fragment in str(e.value)


async def test_too_much_is_refused_before_anything_is_encoded(fake_provider):
    from squirrel_mcp.error_handling import ValidationError

    big = base64.b64encode(b"x" * (MAX_OUTGOING_TOTAL_BYTES + 1)).decode()
    with pytest.raises(ValidationError) as e:
        await resolve_attachments(fake_provider, [{"filename": "big.bin", "content_base64": big}])
    assert "over the" in str(e.value)


async def test_the_total_is_what_counts_not_each_file(fake_provider):
    """Three files under the cap that together exceed it must still be caught --
    the server weighs the message, not the largest part of it."""
    from squirrel_mcp.error_handling import ValidationError

    half = base64.b64encode(b"x" * (MAX_OUTGOING_TOTAL_BYTES // 2)).decode()
    specs = [{"filename": f"f{i}.bin", "content_base64": half} for i in range(3)]
    with pytest.raises(ValidationError):
        await resolve_attachments(fake_provider, specs)


async def test_a_backend_that_cannot_send_files_refuses_instead_of_dropping_them(fake_provider):
    """The whole point of the capability flag. A provider that ignored the
    kwarg would send the mail without the file and report success -- the
    recipient never learns anything was meant to be there."""
    from squirrel_mcp.error_handling import ValidationError

    fake_provider.attachments_supported = False
    with pytest.raises(ValidationError) as e:
        await resolve_attachments(fake_provider, [{"filename": "x.txt", "content_base64": "aGk="}])
    assert "cannot send attachments" in str(e.value)


async def test_a_backend_without_the_flag_at_all_also_refuses():
    """Backends written before this existed have no such attribute; absence
    must read as 'no', not as 'unknown, try it and see'."""
    from squirrel_mcp.error_handling import ValidationError

    class Ancient:
        pass

    with pytest.raises(ValidationError):
        await resolve_attachments(Ancient(), [{"filename": "x.txt", "content_base64": "aGk="}])


# ---- through the tools ---------------------------------------------------- #


async def test_send_hands_the_files_to_the_provider(app_with_tools, fake_provider):
    result = await app_with_tools.call_tool(
        "mail_send",
        {
            "to": "a@b.com", "subject": "Invoice", "body": "See attached.",
            "attachments": [{"filename": "inv.pdf", "content_base64": base64.b64encode(PDF).decode()}],
            "confirm": True,
        },
    )
    (att,) = fake_provider.send_kwargs[-1]["attachments"]
    assert att.filename == "inv.pdf" and att.content == PDF
    # and the caller gets a receipt naming what left the mailbox
    assert "'attachments': ['inv.pdf']" in str(result)


async def test_send_refuses_a_bad_attachment_before_anything_leaves(app_with_tools, fake_provider):
    """Validation sits in front of the confirm gate on purpose: a wrong uid
    must fail while nothing has been sent, not halfway through."""
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await app_with_tools.call_tool(
            "mail_send",
            {"to": "a@b.com", "subject": "s", "body": "b", "confirm": True,
             "attachments": [{"filename": "x.pdf", "content_base64": "@@@"}]},
        )
    assert fake_provider.sent == []


async def test_draft_carries_attachments_too(app_with_tools, fake_provider):
    await app_with_tools.call_tool(
        "mail_create_draft",
        {"to": "a@b.com", "subject": "s", "body": "b",
         "attachments": [{"source_uid": "101", "source_index": 0}]},
    )
    (att,) = fake_provider.draft_kwargs[-1]["attachments"]
    assert att.filename == "doc.pdf"


async def test_editing_a_draft_without_mentioning_files_keeps_them(app_with_tools, fake_provider):
    """The trap: an edit rewrites the whole message. Fixing a typo in the
    covering note must not drop the file the note is about."""
    await app_with_tools.call_tool(
        "mail_edit_draft",
        {"uid": "900", "to": "a@b.com", "subject": "s", "body": "fixed typo"},
    )
    assert fake_provider.draft_kwargs[-1]["attachments"] is None


async def test_editing_a_draft_with_an_empty_list_strips_them(app_with_tools, fake_provider):
    await app_with_tools.call_tool(
        "mail_edit_draft",
        {"uid": "900", "to": "a@b.com", "subject": "s", "body": "b", "attachments": []},
    )
    assert fake_provider.draft_kwargs[-1]["attachments"] == []


async def test_the_tools_advertise_the_argument(app_with_tools):
    tools = {t.name: t for t in await app_with_tools.list_tools()}
    for name in ("mail_send", "mail_create_draft", "mail_edit_draft"):
        assert "attachments" in tools[name].inputSchema["properties"]


# ---- the server's own limit ----------------------------------------------- #


class _FakeServer:
    """Just enough smtplib.SMTP to drive _check_size."""

    def __init__(self, features):
        self.esmtp_features = features
        self.sent = []

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent.append(msg)


def test_size_check_refuses_what_the_server_would_refuse():
    # Soverin advertises SIZE 73400320; pretend a much smaller ceiling.
    server = _FakeServer({"size": "1000"})
    with pytest.raises(MailProviderError) as e:
        SoverinSmtpClient._check_size(server, 5000)
    assert "over this server's" in str(e.value)


def test_size_check_passes_a_message_that_fits():
    SoverinSmtpClient._check_size(_FakeServer({"size": "73400320"}), 5_000_000)


@pytest.mark.parametrize("features", [{}, {"size": "0"}, {"size": "unlimited"}])
def test_a_server_that_states_no_limit_is_left_alone(features):
    """Inventing a ceiling for a server that never named one would manufacture
    the very failure this check exists to prevent."""
    SoverinSmtpClient._check_size(_FakeServer(features), 50_000_000)


def test_the_socket_timeout_grows_with_the_payload():
    """A flat 10s covers a text message and silently demands a 20 Mbit/s
    uplink for 25 MB -- which fails as a timeout that reads like a dead host."""
    assert _timeout_for(0) == 10
    assert _timeout_for(25 * 1024 * 1024) > 300


def test_a_send_that_fits_reaches_the_server(monkeypatch):
    """End to end through the client: build, size-check, deliver."""
    client = SoverinSmtpClient(
        host="smtp.example.net", port=465, username="u", password="p", email="me@example.net"
    )
    server = _FakeServer({"size": "73400320"})
    captured = {}

    def fake_deliver(msg, recipients, size_bytes):
        captured["msg"] = msg
        captured["size"] = size_bytes
        SoverinSmtpClient._check_size(server, size_bytes)

    monkeypatch.setattr(client, "_deliver", fake_deliver)
    client.send(["a@b.com"], "Invoice", "See attached.", attachments=[_att()])
    assert captured["msg"].get_content_type() == "multipart/mixed"
    # The size handed to the check is the real wire size, not the file's.
    assert captured["size"] > len(PDF)


def test_an_oversize_send_fails_as_a_provider_error(monkeypatch):
    client = SoverinSmtpClient(
        host="smtp.example.net", port=465, username="u", password="p", email="me@example.net"
    )

    def fake_deliver(msg, recipients, size_bytes):
        SoverinSmtpClient._check_size(_FakeServer({"size": "100"}), size_bytes)

    monkeypatch.setattr(client, "_deliver", fake_deliver)
    with pytest.raises(MailProviderError) as e:
        client.send(["a@b.com"], "s", "b", attachments=[_att()])
    assert "MB limit" in str(e.value)


def test_smtplib_is_what_actually_carries_it():
    """Neither SMTP nor IMAP knows what an attachment is -- both take one
    opaque RFC 5322 blob. Pinning that keeps anyone from looking for a
    protocol-level attachment feature that does not exist."""
    msg = build_email("me@x.eu", ["a@b.com"], "s", "b", attachments=[_att()])
    raw = msg.as_bytes()
    assert b"Content-Disposition: attachment" in raw
    assert base64.b64encode(PDF)[:20] in raw.replace(b"\n", b"")
    assert isinstance(smtplib.SMTP.sendmail, type(lambda: None))
