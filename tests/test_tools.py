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
    "mail_send_draft",
    "mail_move",
    "mail_flag",
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


async def test_flag_is_a_write_but_not_destructive(app_with_tools):
    """A flag is metadata you can take straight back off, so it announces
    itself as a non-destructive, idempotent write -- and needs no confirm=."""
    tools = {t.name: t for t in await app_with_tools.list_tools()}
    flag = tools["mail_flag"]
    assert flag.annotations.readOnlyHint is False
    assert flag.annotations.destructiveHint is False
    assert flag.annotations.idempotentHint is True
    assert "confirm" not in flag.inputSchema.get("properties", {})


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


async def test_send_reports_the_copy_in_sent(app_with_tools, fake_provider):
    """A send that cannot be found in Sent afterwards reads as a send that
    never happened, so the result says where the copy went."""
    result = await app_with_tools.call_tool(
        "mail_send",
        {"to": "x@y.com", "subject": "hi", "body": "yo", "confirm": True},
    )
    text = str(result)
    assert "saved_to_sent" in text and "True" in text
    assert "Sent" in text


async def test_a_failed_sent_copy_is_reported_not_raised(app_with_tools, fake_provider):
    """The message left. Failing the tool call would invite a second one."""
    fake_provider.sent_copy_ok = False
    result = await app_with_tools.call_tool(
        "mail_send",
        {"to": "x@y.com", "subject": "hi", "body": "yo", "confirm": True},
    )
    assert len(fake_provider.sent) == 1
    structured = result[1] if isinstance(result, tuple) else result
    assert structured["saved_to_sent"] is False
    assert structured["status"] == "Sent"


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


async def test_flag_round_trip_shows_up_in_search(app_with_tools, fake_provider):
    """Setting the flag is only useful if it is then visible: flag, see
    \\Flagged come back from mail_search, unflag, see it gone again."""
    await app_with_tools.call_tool("mail_flag", {"uids": "101", "folder": "INBOX"})
    assert fake_provider.flagged == {"101"}
    assert "\\\\Flagged" in str(await app_with_tools.call_tool("mail_search", {}))

    await app_with_tools.call_tool(
        "mail_flag", {"uids": ["101"], "folder": "INBOX", "flagged": False}
    )
    assert fake_provider.flagged == set()
    assert "\\\\Flagged" not in str(await app_with_tools.call_tool("mail_search", {}))


async def test_search_can_narrow_to_flagged_messages(app_with_tools):
    """"What have I flagged" is a filter, not a folder scan the client sifts."""
    empty = await app_with_tools.call_tool("mail_search", {"flagged_only": True})
    assert "'total': 0" in str(empty) and "\\\\Flagged" not in str(empty)

    await app_with_tools.call_tool("mail_flag", {"uids": "101", "folder": "INBOX"})
    found = await app_with_tools.call_tool("mail_search", {"flagged_only": True})
    assert "\\\\Flagged" in str(found)


async def test_flag_requires_at_least_one_uid(app_with_tools, fake_provider):
    with pytest.raises(Exception) as exc:
        await app_with_tools.call_tool("mail_flag", {"uids": "", "folder": "INBOX"})
    assert "uids" in str(exc.value)
    assert fake_provider.flagged == set()


async def test_draft_reports_the_from_address(app_with_tools):
    result = await app_with_tools.call_tool(
        "mail_draft", {"to": "x@y.com", "subject": "hi", "body": "yo"}
    )
    assert "me@example.com" in str(result)
