"""The IMAP half of replying, without a server.

Two things this layer alone decides, and both fail silently if they are wrong
-- the mail goes out, it just does not join the thread:

* ``reply_headers`` reads the parent's Message-ID and References and hands back
  the chain a reply should carry. It fetches headers only, because a reply
  needs four header lines and not a 4 MB body.
* ``update_draft`` rewrites the message it replaces, so anything not carried
  over is lost. A reply drafted for the user to review would otherwise turn
  back into a new conversation the moment they edited a typo in it.
"""

import pytest

from squirrel_mcp.providers.protocol import MailNotFoundError
from squirrel_mcp.providers.soverin.imap import SoverinImapClient

PARENT = "<CABZfRwFBC1QPc3gd0R@mail.gmail.com>"


class FakeMessage:
    def __init__(self, headers, uid="101"):
        # imap-tools hands headers back lowercased, each value a tuple.
        self.headers = {k.lower(): (v,) for k, v in headers.items()}
        self.uid = uid


class FakeMailBox:
    """Records fetches and appends; hands back scripted messages."""

    def __init__(self, message=None):
        self.selected = None
        self.appended: list = []
        self.deleted: list = []
        self.fetch_calls: list = []
        self._message = message

        manager = self

        class _Folder:
            def set(self, name, readonly=False):
                manager.selected = name

        self.folder = _Folder()

    def fetch(self, *args, **kwargs):
        self.fetch_calls.append(kwargs)
        return iter([self._message] if self._message else [])

    def append(self, raw, folder, flag_set=None):
        self.appended.append(raw.decode())

    def delete(self, uids):
        self.deleted.append(uids)

    def uids(self, criteria, charset="US-ASCII"):
        return ["902"]


def _client(mailbox):
    c = SoverinImapClient(
        host="imap.example.net",
        port=993,
        username="me@example.net",
        password="pw",
        email="me@example.net",
    )
    c._mailbox = mailbox
    return c


# ---- reply_headers -------------------------------------------------------- #


def test_reply_headers_chains_the_parent_onto_its_own_references():
    mb = FakeMailBox(FakeMessage({"Message-ID": PARENT, "References": "<root@x> <mid@x>"}))
    in_reply_to, references = _client(mb).reply_headers("INBOX", "101")
    assert in_reply_to == PARENT
    assert references == ["<root@x>", "<mid@x>", PARENT]
    assert mb.selected == "INBOX"


def test_reply_headers_falls_back_to_in_reply_to_when_there_is_no_chain_yet():
    """The first reply in a thread often carries only In-Reply-To, so starting
    the chain from References alone would drop the thread's root."""
    mb = FakeMailBox(FakeMessage({"Message-ID": PARENT, "In-Reply-To": "<root@x>"}))
    _in_reply_to, references = _client(mb).reply_headers("INBOX", "101")
    assert references == ["<root@x>", PARENT]


def test_reply_headers_on_a_thread_starter():
    mb = FakeMailBox(FakeMessage({"Message-ID": PARENT}))
    assert _client(mb).reply_headers("INBOX", "101") == (PARENT, [PARENT])


def test_a_message_without_a_message_id_replies_unthreaded_rather_than_failing():
    """Legal, rare, and not worth failing a send over -- one message misses the
    thread instead of the whole reply erroring out."""
    mb = FakeMailBox(FakeMessage({"Subject": "no id here"}))
    assert _client(mb).reply_headers("INBOX", "101") == (None, [])


def test_reply_headers_fetches_headers_only():
    mb = FakeMailBox(FakeMessage({"Message-ID": PARENT}))
    _client(mb).reply_headers("INBOX", "101")
    assert mb.fetch_calls[-1]["headers_only"] is True
    assert mb.fetch_calls[-1]["mark_seen"] is False  # reading is not receiving


def test_replying_to_a_message_that_is_gone_says_so():
    with pytest.raises(MailNotFoundError):
        _client(FakeMailBox()).reply_headers("INBOX", "404")


# ---- update_draft --------------------------------------------------------- #


def test_editing_a_reply_draft_keeps_it_in_the_thread():
    mb = FakeMailBox(
        FakeMessage({"In-Reply-To": PARENT, "References": f"<root@x> {PARENT}"}, uid="900")
    )
    _client(mb).update_draft("Drafts", "900", ["anna@example.com"], "Re: Hello", "v2")
    raw = mb.appended[-1]
    assert f"In-Reply-To: {PARENT}" in raw
    assert f"References: <root@x> {PARENT}" in raw
    assert mb.deleted == [["900"]]  # the old revision goes


def test_editing_an_ordinary_draft_invents_no_thread():
    mb = FakeMailBox(FakeMessage({"Subject": "Hello"}, uid="900"))
    _client(mb).update_draft("Drafts", "900", ["anna@example.com"], "Hello", "v2")
    raw = mb.appended[-1]
    assert "In-Reply-To:" not in raw and "References:" not in raw


def test_saving_a_reply_draft_writes_the_headers():
    mb = FakeMailBox()
    _client(mb).save_draft(
        ["anna@example.com"], "Re: Hello", "sure",
        folder="Drafts", in_reply_to=PARENT, references=["<root@x>", PARENT],
    )
    assert f"In-Reply-To: {PARENT}" in mb.appended[-1]
