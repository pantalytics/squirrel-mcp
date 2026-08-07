"""Embedding an image in the body, rather than hanging it off the message.

The follow-up ``OutgoingAttachment`` named when it shipped without an inline
flag: *"inline sending waits for an HTML compose path"*. This is that path, and
the caution that deferred it is the thing being tested -- a
``multipart/related`` holding an image nothing points at renders differently in
every client, so an inline part is only *placed* inline when there is a
``body_html`` that could carry the ``cid:`` reference, and degrades to an
honest attachment when there is not.

Placement is the whole feature and no unit above the MIME layer can see it, so
most of this is tree shapes. ``tests/test_attachments_outgoing.py`` covers the
argument forms and the size/refusal rules; only what inline adds is here.
"""

import base64

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import OutgoingAttachment
from squirrel_mcp.providers.soverin.mime import build_email
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_tools
from squirrel_mcp.tools.mail.attachments import resolve_attachments

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()


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


def leaves(msg):
    """The message's leaf parts as (content_type, filename, cid, disposition)."""
    if not msg.is_multipart():
        return [(msg.get_content_type(), msg.get_filename(),
                 msg.get("Content-ID"), msg.get_content_disposition())]
    out = []
    for part in msg.get_payload():
        out.extend(leaves(part))
    return out


def related_group(msg):
    """The ``multipart/related`` anywhere in the tree, or None.

    Its depth depends on what else is on the message -- an HTML body wraps it
    in ``multipart/alternative``, an ordinary attachment wraps that in
    ``multipart/mixed`` -- and the test is about the group's *contents*, not
    how many layers happen to sit above it.
    """
    for part in msg.walk():
        if part.get_content_type() == "multipart/related":
            return part
    return None


def image(cid="logo"):
    return OutgoingAttachment(
        filename="logo.png", content_type="image/png", content=b"\x89PNG",
        inline=True, content_id=cid,
    )


# ---- the html body -------------------------------------------------------- #
def test_an_html_body_is_an_alternative_never_a_replacement():
    """A plain-text client must still get the message, not an empty one."""
    msg = build_email("me@example.com", ["a@b.com"], "Hi", "text", body_html="<p>rich</p>")
    assert msg.get_content_type() == "multipart/alternative"
    assert [leaf[0] for leaf in leaves(msg)] == ["text/plain", "text/html"]


def test_no_html_and_no_attachments_is_still_one_text_part():
    """Nothing about an ordinary mail changed shape to make room for this."""
    msg = build_email("me@example.com", ["a@b.com"], "Hi", "body")
    assert not msg.is_multipart()
    assert msg.get_content_type() == "text/plain"


# ---- the placement -------------------------------------------------------- #
def test_an_inline_image_sits_inside_multipart_related_next_to_the_html():
    """This placement is what makes ``cid:`` resolve.

    A client resolves the reference within the related group, so the same image
    parked in the outer mixed group alongside the ordinary attachments renders
    in some clients and arrives as a second paperclip in the rest.
    """
    msg = build_email(
        "me@example.com", ["a@b.com"], "Hi", "text",
        body_html='<p><img src="cid:logo"></p>',
        attachments=[image()],
    )
    related = related_group(msg)
    assert related is not None, "the image was not placed in a related group"
    assert [p.get_content_type() for p in related.get_payload()] == [
        "text/html", "image/png",
    ]
    embedded = related.get_payload()[1]
    assert embedded["Content-ID"] == "<logo>"
    assert embedded.get_content_disposition() == "inline"


def test_an_inline_image_with_no_html_degrades_to_an_ordinary_attachment():
    """The honest half of the promise, and the reason this waited for HTML.

    An image nothing can point at is an attachment whatever the caller called
    it, so it is placed as one -- keeping its Content-ID for a client that
    wants to show it anyway.
    """
    msg = build_email(
        "me@example.com", ["a@b.com"], "Hi", "text", attachments=[image()]
    )
    assert msg.get_content_type() == "multipart/mixed"
    assert ("image/png", "logo.png", "<logo>", "inline") in leaves(msg)


def test_inline_and_ordinary_attachments_land_in_different_groups():
    msg = build_email(
        "me@example.com", ["a@b.com"], "Hi", "text",
        body_html='<p><img src="cid:logo"></p>',
        attachments=[
            image(),
            OutgoingAttachment("report.pdf", "application/pdf", b"%PDF"),
        ],
    )
    # The paperclip is a sibling of the whole body; the image is inside it,
    # in the related group next to the HTML that names it.
    assert msg.get_content_type() == "multipart/mixed"
    assert [p.get_content_type() for p in msg.get_payload()] == [
        "multipart/alternative", "application/pdf",
    ]
    related = related_group(msg)
    assert [p.get_content_type() for p in related.get_payload()] == [
        "text/html", "image/png",
    ]
    assert ("image/png", "logo.png", "<logo>", "inline") in leaves(msg)


def test_several_inline_images_all_join_the_related_group():
    msg = build_email(
        "me@example.com", ["a@b.com"], "Hi", "text",
        body_html='<p><img src="cid:a"><img src="cid:b"></p>',
        attachments=[
            OutgoingAttachment("a.png", "image/png", b"A", inline=True, content_id="a"),
            OutgoingAttachment("b.png", "image/png", b"B", inline=True, content_id="b"),
        ],
    )
    related = related_group(msg)
    assert [p.get_content_type() for p in related.get_payload()] == [
        "text/html", "image/png", "image/png",
    ]
    assert [p["Content-ID"] for p in related.get_payload()[1:]] == ["<a>", "<b>"]


def test_inline_does_not_disturb_the_threading_headers():
    """A reply with an embedded image is still a reply."""
    msg = build_email(
        "me@example.com", ["a@b.com"], "Re: Hi", "text",
        body_html='<p><img src="cid:logo"></p>',
        in_reply_to="<parent@x>", references=["<root@x>", "<parent@x>"],
        attachments=[image()],
    )
    assert msg["In-Reply-To"] == "<parent@x>"
    assert msg["References"] == "<root@x> <parent@x>"


# ---- the argument --------------------------------------------------------- #
async def test_an_inline_attachment_always_ends_up_with_a_content_id(fake_provider):
    """Nothing can point at a part with no id, so one is derived from the name."""
    got = await resolve_attachments(
        fake_provider,
        [{"filename": "logo.png", "content_base64": PNG, "inline": True}],
    )
    assert got[0].inline is True
    assert got[0].content_id == "logo-0"


@pytest.mark.parametrize("given", ["logo", "cid:logo", "<logo>"])
async def test_a_content_id_is_read_the_three_ways_a_model_writes_it(
    fake_provider, given
):
    """Bare, as the URL it put in the body, or as the header spelling."""
    got = await resolve_attachments(
        fake_provider,
        [{"filename": "x.png", "content_base64": PNG, "inline": True,
          "content_id": given}],
    )
    assert got[0].content_id == "logo"


async def test_a_content_id_with_unsafe_characters_is_cleaned_not_sent(fake_provider):
    got = await resolve_attachments(
        fake_provider,
        [{"filename": "x.png", "content_base64": PNG, "inline": True,
          "content_id": 'my logo "1"'}],
    )
    assert got[0].content_id == "my-logo-1"


async def test_an_ordinary_attachment_claims_no_content_id(fake_provider):
    """A paperclip has nothing pointing at it and does not need naming."""
    got = await resolve_attachments(
        fake_provider, [{"filename": "r.pdf", "content_base64": PNG}]
    )
    assert got[0].inline is False and got[0].content_id is None


async def test_a_file_already_in_the_mailbox_can_be_embedded_too(fake_provider):
    """source_uid and inline are orthogonal: re-embed a logo from an old mail
    without routing it through the model."""
    got = await resolve_attachments(
        fake_provider,
        [{"source_uid": "412", "source_index": 0, "inline": True, "content_id": "sig"}],
    )
    assert got[0].inline is True and got[0].content_id == "sig"
    assert got[0].content  # the provider's bytes, not the caller's


# ---- the tools ------------------------------------------------------------ #
async def test_send_hands_the_html_body_to_the_provider(app_with_tools, fake_provider):
    await app_with_tools.call_tool(
        "mail_send",
        {
            "to": "a@b.com", "subject": "Hi", "body": "text", "confirm": True,
            "body_html": '<p><img src="cid:logo"></p>',
            "attachments": [
                {"filename": "logo.png", "content_base64": PNG,
                 "inline": True, "content_id": "logo"},
            ],
        },
    )
    kwargs = fake_provider.send_kwargs[-1]
    assert kwargs["body_html"] == '<p><img src="cid:logo"></p>'
    assert kwargs["attachments"][0].inline is True
    assert kwargs["attachments"][0].content_id == "logo"


async def test_a_draft_carries_the_html_body_too(app_with_tools, fake_provider):
    await app_with_tools.call_tool(
        "mail_draft",
        {"to": "a@b.com", "subject": "Hi", "body": "text", "body_html": "<p>rich</p>"},
    )
    assert fake_provider.draft_kwargs[-1]["body_html"] == "<p>rich</p>"


async def test_a_plain_send_still_sends_no_html(app_with_tools, fake_provider):
    await app_with_tools.call_tool(
        "mail_send",
        {"to": "a@b.com", "subject": "Hi", "body": "text", "confirm": True},
    )
    assert fake_provider.send_kwargs[-1]["body_html"] is None
