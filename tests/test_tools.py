"""Tool registration, protocol conformance, and a couple of end-to-end calls
through FastMCP against the fake provider.
"""

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import MailProvider
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_tools

EXPECTED_TOOLS = {
    "mail_list_accounts",
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
    result = await app_with_tools.call_tool(
        "mail_send",
        {"to": "x@y.com", "subject": "hi", "body": "yo", "confirm": True},
    )
    assert len(fake_provider.sent) == 1
    # The result names the account it went out from, so the client can tell
    # the user -- "sent" without a sender is half an answer.
    assert "me@example.com" in str(result)


async def test_list_accounts_names_the_configured_account(app_with_tools):
    """Standalone there is exactly one account, and it is the default."""
    result = await app_with_tools.call_tool("mail_list_accounts", {})
    text = str(result)
    assert "me@example.com" in text and "default" in text


async def test_mail_tools_accept_an_account_argument(app_with_tools):
    """`account` is part of every mail tool's schema, so a client that knows
    about multiple accounts can pass it -- standalone simply has one."""
    tools = {t.name: t for t in await app_with_tools.list_tools()}
    for name in EXPECTED_TOOLS - {"mail_list_accounts"}:
        assert "account" in tools[name].inputSchema.get("properties", {}), name
    # And passing it standalone is harmless.
    result = await app_with_tools.call_tool(
        "mail_list_folders", {"account": "default"}
    )
    assert "INBOX" in str(result)


async def test_draft_reports_the_from_address(app_with_tools):
    result = await app_with_tools.call_tool(
        "mail_draft", {"to": "x@y.com", "subject": "hi", "body": "yo"}
    )
    assert "me@example.com" in str(result)
