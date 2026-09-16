"""One body, and a say in what it is.

``body_format`` replaced a second ``body_html`` argument on the compose tools.
The wire is unchanged -- ``multipart/alternative``, text first -- but a caller
now writes the message once and names its form, instead of writing it twice and
watching the two halves drift apart on the next edit.

What this pins: the mapping in both directions, the derived plain-text half,
and that an unrecognised format is refused rather than guessed at (a body
composed as markdown would otherwise go out as literal asterisks).
"""

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.error_handling import ValidationError
from squirrel_mcp.html_text import html_to_text
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_tools
from squirrel_mcp.tools._common import as_bodies


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


# ---- the split ------------------------------------------------------------ #
def test_text_is_the_default_and_carries_no_html():
    assert as_bodies("plain", None) == ("plain", None)
    assert as_bodies("plain", "text") == ("plain", None)


def test_html_becomes_both_halves():
    text, html = as_bodies("<p>Hoi</p>", "html")
    assert html == "<p>Hoi</p>"
    assert text == "Hoi"


def test_the_format_is_read_loosely():
    assert as_bodies("<p>x</p>", " HTML ")[1] == "<p>x</p>"


def test_an_unknown_format_is_refused():
    with pytest.raises(ValidationError):
        as_bodies("*x*", "markdown")


# ---- the derived plain-text half ------------------------------------------ #
def test_blocks_become_line_breaks():
    assert html_to_text("<p>een</p><p>twee</p>") == "een\n\ntwee"
    assert html_to_text("regel<br>twee") == "regel\ntwee"


def test_list_items_keep_their_shape():
    assert html_to_text("<ul><li>een</li><li>twee</li></ul>") == "- een\n- twee"


def test_entities_are_resolved():
    """&amp; reaching a plain-text client as "&amp;" is the tell that nobody
    converted anything."""
    assert html_to_text("<p>Dank &amp; groet &mdash; Rutger</p>") == "Dank & groet — Rutger"


def test_styling_is_not_content():
    assert html_to_text("<style>p{color:red}</style><p>Hi</p>") == "Hi"


def test_cells_do_not_run_together():
    assert html_to_text("<td>a</td><td>b</td>") == "a b"


# ---- the tools ------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_editing_a_draft_carries_the_format(app_with_tools, fake_provider):
    await app_with_tools.call_tool(
        "mail_edit_draft",
        {"uid": "1", "to": "a@b.com", "subject": "Hi",
         "body": "<p>rich</p>", "body_format": "html"},
    )
    assert fake_provider.draft_kwargs[-1]["body_html"] == "<p>rich</p>"


@pytest.mark.asyncio
async def test_a_draft_defaults_to_plain_text(app_with_tools, fake_provider):
    await app_with_tools.call_tool(
        "mail_edit_draft",
        {"uid": "1", "to": "a@b.com", "subject": "Hi", "body": "plain"},
    )
    assert fake_provider.draft_kwargs[-1]["body_html"] is None
