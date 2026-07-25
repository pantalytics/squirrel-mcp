"""Tool registration, protocol conformance, and a couple of end-to-end calls
through FastMCP against the fake provider.
"""

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import MailProvider
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_tools

EXPECTED_TOOLS = {
    "mail_list_folders",
    "mail_search",
    "mail_read",
    "mail_read_chunk",
    "mail_get_attachment",
    "mail_draft",
    "mail_edit_draft",
    "mail_send",
    "mail_move",
}


@pytest.fixture
def app_with_tools(fake_provider):
    app = create_fastmcp_app()
    cfg = SquirrelConfig(
        mail_email="me@x.eu",
        mail_password="pw",
        imap_host="imap.x.eu",
        smtp_host="smtp.x.eu",
    )
    register_tools(app, fake_provider, cfg)
    return app


def test_fake_provider_satisfies_protocol(fake_provider):
    assert isinstance(fake_provider, MailProvider)


async def test_all_tools_registered(app_with_tools):
    names = {t.name for t in await app_with_tools.list_tools()}
    assert EXPECTED_TOOLS <= names


async def test_send_and_move_are_destructive(app_with_tools):
    tools = {t.name: t for t in await app_with_tools.list_tools()}
    # ToolAnnotations surface on the listed tool.
    assert tools["mail_send"].annotations.destructiveHint is True
    assert tools["mail_move"].annotations.destructiveHint is True
    assert tools["mail_search"].annotations.readOnlyHint is True


async def test_list_folders_call(app_with_tools):
    result = await app_with_tools.call_tool("mail_list_folders", {})
    structured = result[1] if isinstance(result, tuple) else result
    text = str(structured)
    assert "INBOX" in text and "Drafts" in text


async def test_send_without_confirm_is_refused(app_with_tools, fake_provider):
    with pytest.raises(Exception) as exc:
        await app_with_tools.call_tool(
            "mail_send", {"to": "x@y.com", "subject": "hi", "body": "yo"}
        )
    assert "confirm" in str(exc.value).lower()
    assert fake_provider.sent == []  # nothing sent


async def test_send_with_confirm_sends(app_with_tools, fake_provider):
    await app_with_tools.call_tool(
        "mail_send",
        {"to": "x@y.com", "subject": "hi", "body": "yo", "confirm": True},
    )
    assert len(fake_provider.sent) == 1
