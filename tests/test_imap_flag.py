"""The IMAP flag path's command shape, without a server.

``flag`` is the one provider method that builds its own IMAP command instead of
delegating to imap-tools, so the shape of that command is worth pinning on
every branch push -- the GreenMail e2e proves it works, but only in the
integration job. The other half pinned here is what the method deliberately
does *not* do: imap-tools' own ``mb.flag`` follows each STORE with an EXPUNGE,
which would permanently drop anything another client left marked \\Deleted.
"""

import pytest

from squirrel_mcp.providers.protocol import MailProviderError
from squirrel_mcp.providers.soverin.imap import SoverinImapClient


class FakeClient:
    """Stands in for ``imaplib.IMAP4`` -- records what was asked of it."""

    def __init__(self):
        self.uid_calls: list = []
        self.expunged = 0

    def uid(self, *args):
        self.uid_calls.append(args)
        return "OK", [b"1 (UID 101 FLAGS (\\Seen \\Flagged))"]

    def expunge(self):
        self.expunged += 1
        return "OK", [None]


class FakeMailBox:
    def __init__(self):
        self.client = FakeClient()
        self.selected = None

        manager = self

        class _Folder:
            def set(self, name, readonly=False):
                manager.selected = name

        self.folder = _Folder()

    def expunge(self):
        return self.client.expunge()


@pytest.fixture
def client():
    c = SoverinImapClient(
        host="imap.example.net",
        port=993,
        username="me@example.net",
        password="pw",
        email="me@example.net",
    )
    c._mailbox = FakeMailBox()
    return c


def test_flag_stores_the_flagged_marker(client):
    assert client.flag("INBOX", ["101", "102"]) == 2
    mb = client._mailbox
    assert mb.selected == "INBOX"
    assert mb.client.uid_calls == [("STORE", "101,102", "+FLAGS", "(\\Flagged)")]


def test_unflag_removes_it(client):
    assert client.flag("INBOX", ["101"], flagged=False) == 1
    assert client._mailbox.client.uid_calls == [("STORE", "101", "-FLAGS", "(\\Flagged)")]


def test_flag_never_expunges(client):
    """The reason this method exists instead of a call to ``mb.flag``."""
    client.flag("INBOX", ["101"])
    assert client._mailbox.client.expunged == 0


def test_empty_uid_list_touches_nothing(client):
    assert client.flag("INBOX", []) == 0
    assert client._mailbox.client.uid_calls == []


def test_a_uid_that_could_inject_imap_is_refused(client):
    """uids arrive as tool arguments, so they are validated before they are
    pasted into a command string."""
    with pytest.raises(MailProviderError):
        client.flag("INBOX", ["101 (\\Deleted)"])
    assert client._mailbox.client.uid_calls == []


def test_flagged_only_narrows_the_search_criteria(client):
    """The footgun this guards: imap-tools maps ``flagged=False`` to UNFLAGGED,
    which is the opposite of "no filter" -- so the key must be absent, not
    false, when the caller did not ask."""
    build = SoverinImapClient._build_criteria
    assert build(None, False, True, None) == "(FLAGGED)"
    assert build(None, False, False, None) == "ALL"
    assert "UNFLAGGED" not in str(build("invoice", True, False, "2026-07-01"))
    # The query's keys lead now that it compiles to one per term rather than to
    # a single literal; the flag keys are what this test is about.
    assert build("invoice", True, True, None) == '((TEXT "invoice") FLAGGED UNSEEN)'


def test_a_rejected_store_becomes_a_provider_error(client, monkeypatch):
    monkeypatch.setattr(
        client._mailbox.client, "uid", lambda *a: ("NO", [b"permission denied"])
    )
    with pytest.raises(MailProviderError) as e:
        client.flag("INBOX", ["101"])
    assert "flags" in str(e.value).lower()
