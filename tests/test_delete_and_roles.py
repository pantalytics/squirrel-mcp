"""The delete key, and folders that say what they are for.

Two halves of one gap: Squirrel could file a message anywhere but could not
throw one away, and the folder a caller would name to do either was a guess.

**Names are not knowable.** "Archive" is "Archief" on a Dutch host, Trash is
"Prullenbak", and either can sit under INBOX. The server already says which
folder it means -- the SPECIAL-USE attributes of RFC 6154, which
``list_folders`` has been returning in ``flags`` all along and which only the
Sent lookup ever read. So ``mail_list_folders`` now reports a ``role``, and
archiving stays an ordinary ``mail_move`` to the folder that claims to be the
archive rather than to a word someone hoped was right.

**Delete is a move, never an erase.** ``mail_delete`` files into the Trash the
backend found. It does not expunge and does not set ``\\Deleted`` -- the first
would put the message beyond recovery behind a tool that promised the delete
key, and the second would hand another client's pending deletions to the next
expunge that came along, which is the trap ``flag`` already documents.
"""

from __future__ import annotations

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import (
    SPECIAL_USE_FOLDERS,
    FolderInfo,
    MailProviderError,
    folder_role,
)
from squirrel_mcp.providers.soverin.imap import SoverinImapClient
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


@pytest.fixture
def client() -> SoverinImapClient:
    return SoverinImapClient(
        host="imap.example.net",
        port=993,
        username="me@example.net",
        password="pw",
        email="me@example.net",
    )


DUTCH = [
    FolderInfo(name="INBOX", flags=["\\HasNoChildren"]),
    FolderInfo(name="Verzonden items", flags=["\\Sent"]),
    FolderInfo(name="Prullenbak", flags=["\\Trash"]),
    FolderInfo(name="Archief", flags=["\\HasNoChildren", "\\Archive"]),
    FolderInfo(name="Ongewenste e-mail", flags=["\\Junk"]),
    # A decoy: the English name exists but is not what the server means.
    FolderInfo(name="Archive", flags=["\\HasNoChildren"]),
]


# ---- reading the role off the flags --------------------------------------- #
def test_a_folder_reports_the_role_its_flags_claim():
    assert folder_role(["\\HasNoChildren", "\\Archive"]) == "archive"
    assert folder_role(["\\Trash"]) == "trash"


def test_an_ordinary_folder_claims_nothing():
    """Null, not a guess -- most folders are somebody's own filing."""
    assert folder_role(["\\HasNoChildren"]) is None
    assert folder_role([]) is None


def test_the_flag_is_matched_case_insensitively():
    """Servers spell it \\Archive, \\archive and \\ARCHIVE; RFC 6154 does not
    care and neither may we."""
    assert folder_role(["\\ARCHIVE"]) == "archive"


# ---- resolving a role to a folder ----------------------------------------- #
@pytest.mark.parametrize(
    "role,expected",
    [("sent", "Verzonden items"), ("trash", "Prullenbak"), ("archive", "Archief")],
)
def test_the_special_folder_comes_from_the_flag_not_the_name(
    client, monkeypatch, role, expected
):
    """The decoy is the point: an English "Archive" folder exists here and is
    still the wrong answer, because the server flagged a different one."""
    monkeypatch.setattr(client, "list_folders", lambda: list(DUTCH))
    assert client.special_folder(role) == expected


def test_a_server_advertising_nothing_falls_back_to_the_usual_name(client, monkeypatch):
    monkeypatch.setattr(
        client, "list_folders", lambda: [FolderInfo(name="INBOX", flags=[])]
    )
    assert client.special_folder("trash") == "Trash"
    assert client.special_folder("archive") == "Archive"


def test_a_listing_failure_is_not_cached(client, monkeypatch):
    """One bad LIST must not pin the fallback for the life of the session --
    the same rule the Sent lookup was written with."""
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("connection reset")
        return list(DUTCH)

    monkeypatch.setattr(client, "list_folders", flaky)
    assert client.special_folder("trash") == "Trash"  # fallback
    assert client.special_folder("trash") == "Prullenbak"  # looked again


def test_a_resolved_folder_is_cached(client, monkeypatch):
    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return list(DUTCH)

    monkeypatch.setattr(client, "list_folders", counted)
    client.special_folder("trash")
    client.special_folder("trash")
    assert calls["n"] == 1


def test_each_role_is_cached_separately(client, monkeypatch):
    monkeypatch.setattr(client, "list_folders", lambda: list(DUTCH))
    assert client.special_folder("trash") == "Prullenbak"
    assert client.special_folder("archive") == "Archief"


def test_an_unknown_role_is_refused_naming_the_real_ones(client):
    with pytest.raises(MailProviderError, match="archive"):
        client.special_folder("bin")


def test_the_sent_lookup_still_answers_through_the_table(client, monkeypatch):
    """`sent_folder` predates this and is what files the copy in Sent; it must
    keep answering exactly as it did."""
    monkeypatch.setattr(client, "list_folders", lambda: list(DUTCH))
    assert client.sent_folder() == "Verzonden items"


# ---- delete is a move ------------------------------------------------------ #
def test_delete_moves_to_the_trash_it_found(client, monkeypatch):
    monkeypatch.setattr(client, "list_folders", lambda: list(DUTCH))
    moves = []
    monkeypatch.setattr(
        client, "move", lambda f, u, d: moves.append((f, u, d)) or len(u)
    )
    count, target = client.delete("INBOX", ["1", "2"])
    assert moves == [("INBOX", ["1", "2"], "Prullenbak")]
    assert (count, target) == (2, "Prullenbak")


def test_deleting_out_of_trash_says_so_rather_than_erasing(client, monkeypatch):
    """The one thing this tool must never quietly become. Moving a message onto
    itself is not the ask, and expunging is a different promise."""
    monkeypatch.setattr(client, "list_folders", lambda: list(DUTCH))
    with pytest.raises(MailProviderError, match="already in Prullenbak"):
        client.delete("Prullenbak", ["1"])


def test_delete_never_expunges(client, monkeypatch):
    """It goes through `move`, which is a UID MOVE. Nothing in this path may
    reach for EXPUNGE -- another client's \\Deleted messages are not ours."""
    monkeypatch.setattr(client, "list_folders", lambda: list(DUTCH))
    monkeypatch.setattr(client, "move", lambda f, u, d: len(u))
    expunged = []
    monkeypatch.setattr(client, "_run", lambda op: expunged.append(op))
    client.delete("INBOX", ["1"])
    assert expunged == []


# ---- the tools ------------------------------------------------------------- #
async def test_list_folders_tells_the_caller_which_one_is_the_archive(app_with_tools):
    result = await app_with_tools.call_tool("mail_list_folders", {})
    payload = result[1] if isinstance(result, tuple) else result
    by_name = {f["name"]: f.get("role") for f in payload["folders"]}
    assert by_name["Archief"] == "archive"
    assert by_name["Prullenbak"] == "trash"
    # The English decoy carries no flag, so it claims nothing.
    assert by_name["Archive"] is None
    assert by_name["INBOX"] is None


async def test_delete_refuses_without_confirm_and_moves_nothing(
    app_with_tools, fake_provider
):
    with pytest.raises(Exception, match="confirm=true"):
        await app_with_tools.call_tool("mail_delete", {"uids": "101"})
    assert fake_provider.deleted == []


async def test_delete_reports_where_the_messages_went(app_with_tools, fake_provider):
    """`trash_folder` is the whole answer to "where did it go" -- without it
    the user is told a message is gone and not where from."""
    result = await app_with_tools.call_tool(
        "mail_delete", {"uids": "101,102", "confirm": True}
    )
    payload = result[1] if isinstance(result, tuple) else result
    assert fake_provider.deleted == [("INBOX", ["101", "102"])]
    assert payload["deleted"] == 2
    assert payload["trash_folder"] == "Prullenbak"
    assert payload["source_folder"] == "INBOX"


async def test_delete_needs_at_least_one_uid(app_with_tools):
    with pytest.raises(Exception, match="uids"):
        await app_with_tools.call_tool("mail_delete", {"uids": [], "confirm": True})


async def test_a_backend_that_cannot_delete_is_told_what_will_work(
    app_with_tools, fake_provider, monkeypatch
):
    monkeypatch.delattr(type(fake_provider), "delete")
    with pytest.raises(Exception, match="mail_move"):
        await app_with_tools.call_tool("mail_delete", {"uids": "101", "confirm": True})


def test_every_role_has_a_flag_and_a_fallback():
    """The table is the only place these pair up; a half-filled row would
    resolve to None and move mail to a folder called 'None'."""
    for role, (attribute, fallback) in SPECIAL_USE_FOLDERS.items():
        assert attribute.startswith("\\") and attribute.islower(), role
        assert fallback and fallback[0].isupper(), role
