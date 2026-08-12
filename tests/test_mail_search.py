"""What ``mail_search`` puts on the wire, and what it does when nothing matches.

Two seams, one bug. Below the tool: a multi-word query has to leave as several
IMAP keys rather than one literal, because a literal is what missed a contact's
name over a doubled space. Above it: a search that still finds nothing has to
say so by widening and reporting, not by returning an empty page that reads as
"you have no mail from her".
"""

from __future__ import annotations

import json
from typing import List, Optional, Tuple

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import MessageSummary
from squirrel_mcp.providers.soverin.imap import SoverinImapClient
from squirrel_mcp.search_query import MailQuery, parse
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools.handler import register_tools
from squirrel_mcp.tools.mail.query import MAX_WIDENING_STEPS

build = SoverinImapClient._build_criteria


# ---- what leaves for the server ------------------------------------------ #
def test_a_multi_word_query_becomes_one_key_per_word():
    """The fix. ``TEXT "Iris van 't Klooster"`` is a substring of the raw
    message and misses a signature with a second space in it; three keys the
    server ANDs do not care about the spacing, the order or a header fold."""
    criteria = str(build("Iris van 't Klooster", False, False, None))
    assert criteria == '((TEXT "Iris") (TEXT "van") (TEXT "Klooster"))'


def test_a_quoted_phrase_still_goes_out_whole():
    assert str(build('"fiscal localization"', False, False, None)) == (
        '((TEXT "fiscal localization"))'
    )


def test_a_scoped_term_uses_the_real_imap_key():
    """``from:iris`` is answered by the server against the From header, not by
    us against a page of everything mentioning her."""
    criteria = str(build("from:iris subject:invoice", False, False, None))
    assert '(FROM "iris")' in criteria
    assert '(SUBJECT "invoice")' in criteria
    assert "TEXT" not in criteria


def test_negation_and_or_compile_to_imap_keys():
    assert "NOT" in str(build("invoice -newsletter", False, False, None))
    either = str(build("invoice OR factuur", False, False, None))
    assert either.startswith('((OR (TEXT "invoice") (TEXT "factuur")))')


def test_the_other_filters_still_ride_along():
    criteria = str(build("invoice", True, True, "2026-07-01"))
    assert '(TEXT "invoice")' in criteria
    assert "UNSEEN" in criteria and "FLAGGED" in criteria and "SINCE" in criteria


def test_a_query_of_pure_punctuation_is_searched_for_not_dropped():
    """Nothing survives tokenizing ``+++``, and listing the whole folder as if
    no query had been given would be the wrong answer to a real question."""
    assert str(build("+++", False, False, None)) == '((TEXT "+++"))'


def test_no_query_is_still_a_plain_listing():
    assert build(None, False, False, None) == "ALL"


# ---- the widening ladder, above the provider ----------------------------- #
class RecordingProvider:
    """A mailbox holding exactly the messages given, matched the honest way.

    It answers on the *parsed* query rather than the raw string, which is the
    point: it stands in for a backend that has compiled the grammar, and its
    ``calls`` are the round trips the tool layer decided to spend.
    """

    def __init__(self, messages: List[MessageSummary]):
        self.messages = messages
        self.calls: List[List[str]] = []
        self.connected = True
        self._email = "me@example.com"

    @property
    def email(self) -> str:
        return self._email

    @property
    def is_authenticated(self) -> bool:
        return self.connected

    def authenticate(self) -> None:
        self.connected = True

    def search(
        self,
        folder: str,
        query: Optional[str] = None,
        *,
        unseen_only: bool = False,
        flagged_only: bool = False,
        since: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
        parsed: Optional[MailQuery] = None,
    ) -> Tuple[List[MessageSummary], int]:
        parsed = parsed or parse(query)
        self.calls.append(parsed.describe())
        hits = [m for m in self.messages if self._matches(parsed, m)]
        return hits[offset:offset + limit], len(hits)

    @staticmethod
    def _matches(parsed: MailQuery, msg: MessageSummary) -> bool:
        # A literal over the raw message, exactly like an IMAP server: this is
        # the behaviour the terms have to survive.
        raw = f"{msg.subject}\n{msg.from_addr}\n{msg.preview}"
        for clause in parsed.clauses:
            hit = any(t.wire.lower() in raw.lower() for t in clause.terms)
            if clause.negated and hit:
                return False
            if not clause.negated and not hit:
                return False
        return True


def _message(**kw) -> MessageSummary:
    base = dict(
        uid="1", folder="INBOX", subject="", from_addr="", to_addrs=[],
        date="Mon, 01 Jan 2026 10:00:00 +0000", flags=[], size=1,
        has_attachments=False, preview="",
    )
    base.update(kw)
    return MessageSummary(**base)


def _app(provider):
    app = create_fastmcp_app()
    register_tools(
        app,
        provider,
        SquirrelConfig(
            mail_email="me@x.eu", mail_password="pw",
            imap_host="imap.x.eu", smtp_host="smtp.x.eu",
        ),
    )
    return app


async def _search(app, **kw) -> dict:
    result = await app.call_tool("mail_search", kw)
    payload = result[1] if isinstance(result, tuple) else result
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload


@pytest.mark.anyio
async def test_the_reported_bug_is_found_in_one_round_trip():
    """The signature carries a doubled space. The words are all there, so
    nothing has to widen and nothing is reported as approximate."""
    iris = _message(
        subject="Re: Fiscal Localization",
        from_addr="iris@thelekkercompany.com",
        preview="Super!\r\nIris  van 't Klooster\r\nOperations manager",
    )
    provider = RecordingProvider([iris, _message(subject="unrelated")])
    out = await _search(_app(provider), query="Iris van 't Klooster")

    assert [m["uid"] for m in out["messages"]] == ["1"]
    assert out["matched"] == "all"
    assert out["dropped_terms"] == []
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_a_missing_word_widens_and_says_which_one():
    """Better a near miss the caller is told about than an empty page that
    reads as "there is no such mail"."""
    provider = RecordingProvider([_message(subject="Invoice 42 from Klooster")])
    out = await _search(_app(provider), query="Klooster invoice quarterly")

    assert len(out["messages"]) == 1
    assert out["matched"] == "partial"
    assert "quarterly" in out["dropped_terms"]
    assert "Klooster" not in out["dropped_terms"]
    assert len(provider.calls) > 1


@pytest.mark.anyio
async def test_a_query_that_matches_nothing_at_all_says_none():
    provider = RecordingProvider([_message(subject="Invoice 42")])
    out = await _search(_app(provider), query="capybara marzipan")

    assert out["messages"] == []
    assert out["matched"] == "none"


@pytest.mark.anyio
async def test_widening_is_bounded():
    """A query whose words are simply absent costs a few requests, never a
    crawl of the folder."""
    provider = RecordingProvider([_message(subject="Invoice 42")])
    await _search(_app(provider), query="one two three four five six seven")
    assert len(provider.calls) <= 1 + MAX_WIDENING_STEPS


@pytest.mark.anyio
async def test_paging_past_the_end_does_not_re_run_the_ladder():
    """An empty page 3 is the end of the results, not a failed match."""
    provider = RecordingProvider([_message(subject="Invoice 42")])
    out = await _search(_app(provider), query="invoice missingword", offset=50)

    assert out["matched"] == "all"
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_a_provider_that_never_heard_of_parsed_still_works(fake_provider):
    """``parsed`` is an addition to the protocol: the fixture's provider has the
    old signature, and offering it the argument must not be what breaks it."""
    out = await _search(_app(fake_provider), query="anything at all")
    assert out["messages"]


@pytest.mark.anyio
async def test_an_old_provider_is_not_asked_the_same_question_five_times():
    """Widening weakens `parsed` while `raw` stays what the caller typed, so a
    backend that reads only `raw` would be asked the identical question once
    per rung and answer nothing every time."""

    class OldProvider(RecordingProvider):
        def search(self, folder, query=None, **kw):  # no `parsed`
            self.calls.append([query or ""])
            return [], 0

    provider = OldProvider([])
    out = await _search(_app(provider), query="Klooster invoice quarterly")
    assert out["messages"] == []
    assert len(provider.calls) == 1
