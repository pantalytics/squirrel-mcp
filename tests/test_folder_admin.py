"""Making, renaming and throwing away a folder.

``mail_list_folders`` read them and ``mail_move`` filed into them, but a folder
itself could only be made in another mail client first -- so "put these in a
folder called Belastingdienst" was a reasonable ask this server could not
answer.

Three decisions hold these tools up, and this file is their record.

**A path is not the caller's to type.** The hierarchy delimiter is "/" on one
server and "." on the next, and on a mailbox where everything lives under INBOX
a bare top-level name is simply refused. So ``create`` takes a *parent folder*
and the backend joins it with the delimiter it read off LIST -- the same "a name
is not knowable" rule the SPECIAL-USE roles are written with.

**The mailbox's own furniture is refused, not gated.** INBOX and the five
special-use folders are where mail is filed without anybody asking, this
package's own Sent copy included. A confirm prompt cannot explain what breaks,
so renaming or deleting one is answered with a no and a reason.

**Delete is the one thing here that cannot be undone.** IMAP's DELETE takes the
folder's messages with it and no Trash catches them -- unlike ``mail_delete``,
which is a move. So an empty folder is the only one this will delete, and it
says how many messages are in the way. The confirm gate answers "did the user
ask for this"; the refusal answers "can they get it back".
"""

from __future__ import annotations

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import (
    FolderInfo,
    MailNotFoundError,
    MailProviderError,
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


class FakeFolders:
    """The folder half of an imap-tools mailbox, recording what it was told."""

    def __init__(self, listing, current="INBOX", counts=None, fail=None):
        self.listing = listing
        self.current = current
        self.counts = counts or {}
        self.fail = fail  # an exception class to raise from create/rename/delete
        self.calls: list = []

    def get(self):
        return self.current

    def set(self, folder, readonly=False):
        self.current = folder

    def create(self, folder):
        self.calls.append(("create", folder))
        if self.fail:
            raise self.fail("NO", "OK")

    def rename(self, old, new):
        self.calls.append(("rename", old, new))
        if self.fail:
            raise self.fail("NO", "OK")

    def delete(self, folder):
        self.calls.append(("delete", folder))
        if self.fail:
            raise self.fail("NO", "OK")

    def status(self, folder, options=None):
        self.calls.append(("status", folder))
        return {"MESSAGES": self.counts.get(folder, 0)}


class FakeMailbox:
    def __init__(self, folders: FakeFolders):
        self.folder = folders


# A dotted namespace, which is exactly the shape a caller cannot guess: every
# folder hangs off INBOX and the delimiter is not "/".
DOTTED = [
    FolderInfo(name="INBOX", delimiter=".", flags=[]),
    FolderInfo(name="INBOX.Sent", delimiter=".", flags=["\\Sent"]),
    FolderInfo(name="INBOX.Prullenbak", delimiter=".", flags=["\\Trash"]),
    FolderInfo(name="INBOX.Klanten", delimiter=".", flags=[]),
    FolderInfo(name="INBOX.Klanten.Acme", delimiter=".", flags=[]),
]


@pytest.fixture
def client(monkeypatch) -> SoverinImapClient:
    c = SoverinImapClient(
        host="imap.example.net",
        port=993,
        username="me@example.net",
        password="pw",
        email="me@example.net",
    )
    monkeypatch.setattr(c, "list_folders", lambda: list(DOTTED))
    return c


def wire(client, monkeypatch, **kwargs) -> FakeFolders:
    """Point the client's one blocking seam at a fake mailbox."""
    folders = FakeFolders(list(DOTTED), **kwargs)
    monkeypatch.setattr(client, "_run", lambda op: op(FakeMailbox(folders)))
    return folders


# ---- create ---------------------------------------------------------------- #
def test_a_parent_is_joined_with_the_servers_own_delimiter(client, monkeypatch):
    """The whole reason ``parent`` is an argument: "/" here would make a folder
    literally called "INBOX/Belastingdienst" on a dotted server."""
    fake = wire(client, monkeypatch)
    name, created = client.create_folder("Belastingdienst", parent="INBOX")
    assert (name, created) == ("INBOX.Belastingdienst", True)
    assert fake.calls == [("create", "INBOX.Belastingdienst")]


def test_a_name_that_already_carries_its_parent_is_not_doubled(client, monkeypatch):
    fake = wire(client, monkeypatch)
    name, _ = client.create_folder("INBOX.Belastingdienst", parent="INBOX")
    assert name == "INBOX.Belastingdienst"
    assert fake.calls == [("create", "INBOX.Belastingdienst")]


def test_creating_a_folder_that_is_already_there_is_not_a_failure(client, monkeypatch):
    """Asked for, and already true. ``created=False`` is how the caller is told
    not to report a new folder."""
    fake = wire(client, monkeypatch)
    assert client.create_folder("Klanten", parent="INBOX") == ("INBOX.Klanten", False)
    assert fake.calls == []


def test_an_unknown_parent_is_named_rather_than_created_silently(client, monkeypatch):
    wire(client, monkeypatch)
    with pytest.raises(MailNotFoundError, match="Belasting"):
        client.create_folder("Q1", parent="INBOX.Belasting")


def test_an_empty_name_is_refused(client, monkeypatch):
    wire(client, monkeypatch)
    with pytest.raises(MailProviderError, match="needs a name"):
        client.create_folder("   ")


def test_a_refused_create_says_what_usually_fixes_it(client, monkeypatch):
    """The failure a top-level create actually hits, and the one-line answer:
    this mailbox keeps everything under INBOX."""
    from imap_tools.errors import MailboxFolderCreateError

    wire(client, monkeypatch, fail=MailboxFolderCreateError)
    with pytest.raises(MailProviderError, match='parent="INBOX"'):
        client.create_folder("Belastingdienst")


# ---- rename ---------------------------------------------------------------- #
def test_a_bare_new_name_leaves_the_folder_where_it_is(client, monkeypatch):
    """"Call it Facturen" is about the name. Moving it to the root of a
    namespace the caller cannot see is a different ask entirely."""
    fake = wire(client, monkeypatch)
    assert client.rename_folder("INBOX.Klanten", "Facturen") == "INBOX.Facturen"
    assert fake.calls == [("rename", "INBOX.Klanten", "INBOX.Facturen")]


def test_a_new_name_with_a_delimiter_is_taken_as_given(client, monkeypatch):
    fake = wire(client, monkeypatch)
    assert client.rename_folder("INBOX.Klanten", "INBOX.Archief.Klanten") == (
        "INBOX.Archief.Klanten"
    )
    assert fake.calls == [("rename", "INBOX.Klanten", "INBOX.Archief.Klanten")]


@pytest.mark.parametrize("name", ["INBOX", "INBOX.Sent", "INBOX.Prullenbak"])
def test_the_mailboxes_own_furniture_cannot_be_renamed(client, monkeypatch, name):
    fake = wire(client, monkeypatch)
    with pytest.raises(MailProviderError):
        client.rename_folder(name, "Iets anders")
    assert fake.calls == []


def test_renaming_onto_an_existing_folder_is_refused(client, monkeypatch):
    fake = wire(client, monkeypatch)
    with pytest.raises(MailProviderError, match="already a folder"):
        client.rename_folder("INBOX.Klanten", "INBOX.Sent")
    assert fake.calls == []


def test_renaming_an_unknown_folder_says_so(client, monkeypatch):
    wire(client, monkeypatch)
    with pytest.raises(MailNotFoundError):
        client.rename_folder("INBOX.Weg", "INBOX.Er")


def test_renaming_to_the_same_name_does_nothing(client, monkeypatch):
    fake = wire(client, monkeypatch)
    assert client.rename_folder("INBOX.Klanten", "Klanten") == "INBOX.Klanten"
    assert fake.calls == []


# ---- delete ---------------------------------------------------------------- #
def test_an_empty_folder_is_deleted(client, monkeypatch):
    fake = wire(client, monkeypatch, counts={"INBOX.Klanten.Acme": 0})
    assert client.delete_folder("INBOX.Klanten.Acme") == "INBOX.Klanten.Acme"
    assert ("delete", "INBOX.Klanten.Acme") in fake.calls


def test_a_folder_with_mail_in_it_is_refused_with_the_count(client, monkeypatch):
    """The one irreversible thing in this package, and the reason this tool is
    stricter than mail_delete: DELETE takes the messages and no Trash catches
    them."""
    fake = wire(client, monkeypatch, counts={"INBOX.Klanten.Acme": 12})
    with pytest.raises(MailProviderError, match="12 message"):
        client.delete_folder("INBOX.Klanten.Acme")
    assert ("delete", "INBOX.Klanten.Acme") not in fake.calls


def test_a_folder_with_folders_in_it_names_them(client, monkeypatch):
    fake = wire(client, monkeypatch)
    with pytest.raises(MailProviderError, match="INBOX.Klanten.Acme"):
        client.delete_folder("INBOX.Klanten")
    assert ("delete", "INBOX.Klanten") not in fake.calls


@pytest.mark.parametrize("name", ["INBOX", "INBOX.Sent", "INBOX.Prullenbak"])
def test_the_mailboxes_own_furniture_cannot_be_deleted(client, monkeypatch, name):
    fake = wire(client, monkeypatch)
    with pytest.raises(MailProviderError):
        client.delete_folder(name)
    assert ("delete", name) not in fake.calls


def test_deleting_an_unknown_folder_says_so(client, monkeypatch):
    wire(client, monkeypatch)
    with pytest.raises(MailNotFoundError):
        client.delete_folder("INBOX.Weg")


def test_the_selected_folder_is_stepped_out_of_first(client, monkeypatch):
    """Servers disagree about deleting the mailbox you are standing in."""
    fake = wire(client, monkeypatch, current="INBOX.Klanten.Acme")
    client.delete_folder("INBOX.Klanten.Acme")
    assert fake.current == "INBOX"


def test_a_server_that_will_not_answer_status_is_counted_by_hand(client, monkeypatch):
    """No STATUS, no trust: the emptiness check falls back to counting uids
    rather than deleting a folder on the strength of a failed command."""
    from imap_tools.errors import MailboxFolderStatusError

    class NoStatus(FakeFolders):
        def status(self, folder, options=None):
            raise MailboxFolderStatusError("NO", "OK")

    fake = NoStatus(list(DOTTED))
    mailbox = FakeMailbox(fake)
    mailbox.uids = lambda criteria: ["1", "2"]  # type: ignore[attr-defined]
    monkeypatch.setattr(client, "_run", lambda op: op(mailbox))
    with pytest.raises(MailProviderError, match="2 message"):
        client.delete_folder("INBOX.Klanten.Acme")


# ---- the tools -------------------------------------------------------------- #
async def test_create_reports_the_full_name_to_move_into(app_with_tools, fake_provider):
    result = await app_with_tools.call_tool(
        "mail_create_folder", {"name": "Belastingdienst", "parent": "INBOX"}
    )
    payload = result[1] if isinstance(result, tuple) else result
    assert payload["folder"] == "INBOX/Belastingdienst"
    assert payload["created"] is True
    assert "INBOX/Belastingdienst" in fake_provider.folders


async def test_creating_an_existing_folder_says_it_was_already_there(app_with_tools):
    result = await app_with_tools.call_tool("mail_create_folder", {"name": "Archive"})
    payload = result[1] if isinstance(result, tuple) else result
    assert (payload["folder"], payload["created"]) == ("Archive", False)


async def test_create_needs_a_name(app_with_tools):
    with pytest.raises(Exception, match="name"):
        await app_with_tools.call_tool("mail_create_folder", {"name": " "})


async def test_rename_reports_both_names(app_with_tools, fake_provider):
    result = await app_with_tools.call_tool(
        "mail_rename_folder", {"name": "Archive", "new_name": "Oud"}
    )
    payload = result[1] if isinstance(result, tuple) else result
    assert (payload["folder"], payload["previous_name"]) == ("Oud", "Archive")
    assert "Archive" not in fake_provider.folders


async def test_delete_refuses_without_confirm_and_removes_nothing(
    app_with_tools, fake_provider
):
    with pytest.raises(Exception, match="confirm=true"):
        await app_with_tools.call_tool("mail_delete_folder", {"name": "Archive"})
    assert "Archive" in fake_provider.folders


async def test_delete_removes_an_empty_folder(app_with_tools, fake_provider):
    result = await app_with_tools.call_tool(
        "mail_delete_folder", {"name": "Archive", "confirm": True}
    )
    payload = result[1] if isinstance(result, tuple) else result
    assert payload["folder"] == "Archive"
    assert "Archive" not in fake_provider.folders


async def test_delete_passes_the_backends_refusal_on(app_with_tools, fake_provider):
    """The count comes from the backend; the tool must not swallow it."""
    fake_provider.folders["Archive"]["messages"] = 4
    with pytest.raises(Exception, match="still holds"):
        await app_with_tools.call_tool(
            "mail_delete_folder", {"name": "Archive", "confirm": True}
        )
    assert "Archive" in fake_provider.folders


@pytest.mark.parametrize(
    "tool,args,expected",
    [
        ("mail_create_folder", {"name": "X"}, "mail client"),
        ("mail_rename_folder", {"name": "Archive", "new_name": "X"}, "mail client"),
        ("mail_delete_folder", {"name": "Archive", "confirm": True}, "mail client"),
    ],
)
async def test_a_backend_without_folder_writes_is_told_rather_than_crashed_into(
    app_with_tools, fake_provider, monkeypatch, tool, args, expected
):
    """The attachments rule: a capability is read with getattr and refused by
    name, so a backend from before this existed is not asked to guess."""
    for method in ("create_folder", "rename_folder", "delete_folder"):
        monkeypatch.delattr(type(fake_provider), method)
    with pytest.raises(Exception, match=expected):
        await app_with_tools.call_tool(tool, args)


def test_an_unflagged_trash_is_protected_by_the_fallback_name(monkeypatch):
    """GreenMail, and plenty of real servers, advertise no SPECIAL-USE at all.
    The folder this package still files deletes into is the conventional name,
    so that is the one that must not be deletable either."""
    client = SoverinImapClient(
        host="imap.example.net", port=993, username="me", password="pw", email="me@x.eu"
    )
    plain = [
        FolderInfo(name="INBOX", delimiter="/", flags=[]),
        FolderInfo(name="Trash", delimiter="/", flags=[]),
        FolderInfo(name="Sent", delimiter="/", flags=[]),
    ]
    monkeypatch.setattr(client, "list_folders", lambda: list(plain))
    fake = FakeFolders(plain)
    monkeypatch.setattr(client, "_run", lambda op: op(FakeMailbox(fake)))
    with pytest.raises(MailProviderError, match="trash folder"):
        client.delete_folder("Trash")
    assert fake.calls == []
