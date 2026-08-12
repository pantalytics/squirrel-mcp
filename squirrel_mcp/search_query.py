"""The mail search query language -- one grammar, every backend.

Why this module exists
----------------------
``mail_search`` used to hand its ``query`` to the backend as one opaque string:
IMAP got ``TEXT "Iris van 't Klooster"``, Graph got ``$search="Iris van 't
Klooster"``. Two things follow from that, and both bite.

**A literal phrase is the wrong default.** IMAP's ``TEXT`` is a substring match
over the raw message, so a name that reaches the mailbox with a double space
(``Iris  van 't Klooster`` -- mail signatures are full of them), a curly
apostrophe, or a header fold between two of its words simply is not there.
Neither is a name the sender writes ``Klooster, Iris``. The search returns
nothing and there is no hint that the words were all present, four characters
apart.

**And the fallback is undefined.** RFC 3501 lets a server "implement flexible
matching" for ``TEXT``, so what a literal query does is the server's business:
one host word-matches and ignores order, the next does strict substring, Graph
runs KQL keywords. A tool whose meaning depends on who is answering is a
support case nobody can reproduce -- the same rule the calendar and attachment
seams are held to.

So the query is parsed *here*, once, into something both backends compile:

    iris klooster          both words, anywhere, any order  (AND -- the default)
    "fiscal localization"  those words, in that order, as a phrase
    from:iris              the sender, not every mail mentioning her
    to: cc: subject: body: the other fields
    -newsletter            must not appear
    invoice OR factuur     either one
    has:attachment         only messages carrying a file

Unquoted words AND rather than forming a phrase, which is what Gmail, Outlook
and every model prompting them already assume -- and it is the whole fix for
the spacing bug: ``iris`` and ``klooster`` as two keys match a signature no
literal ever will.

Three rules keep the two backends honest:

* **The server query is always at least as broad as the query.** Folding
  accents, apostrophes and whitespace is something we can do to a string in
  memory and cannot ask an IMAP server to do to a mailbox, so a term that
  cannot be sent faithfully is *widened* on the way out (``José`` leaves as
  ``Jos``), never narrowed.
* **Anything we can see, we verify exactly.** A summary carries the subject,
  the sender, the recipients and the attachment flag, so ``from:``, ``to:``,
  ``subject:`` and ``has:`` are re-checked here against the folded text and the
  broadening above is taken back. What a summary does *not* carry -- the body,
  cc -- stays the server's word, because rejecting a message on a body we only
  have 200 characters of would drop real matches.
* **Widening is reported, never silent.** ``relax()`` gives up the least
  distinctive term so a search that would return nothing returns the near miss
  instead, and ``SearchResult.matched`` says which pass answered.

Nothing here imports a backend. ``providers/soverin/imap.py`` compiles a
``MailQuery`` into IMAP keys and the admin package's Graph provider compiles the
same object into KQL, so the grammar has exactly one definition.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Iterator, List, Optional, Sequence, Tuple

__all__ = [
    "FIELDS",
    "MailQuery",
    "Clause",
    "Term",
    "fold",
    "parse",
    "tokenize",
    "verify",
    "widen",
]

# The fields a term can be scoped to. "text" is the unscoped default: the whole
# message, headers and body, which is what both backends search by default.
FIELDS = ("text", "from", "to", "cc", "subject", "body")

# Which of those a MessageSummary can actually prove. Everything else is taken
# on the server's word -- see the module docstring.
VERIFIABLE = ("from", "to", "subject")

# A term shorter than this is noise: the ``t`` left over from ``van 't`` matches
# most of a mailbox and narrows nothing, and sending it costs a real server-side
# scan. Dropped at parse time rather than at match time so it never reaches a
# backend.
MIN_TERM_LEN = 2

# Given up first when a search finds nothing and has to widen. Words that carry
# no weight in a mailbox, in the two languages this product is used in.
_STOPWORDS = frozenset(
    """
    a an and are as at be by for from has have he in is it its of on or that the
    to was were will with
    aan als bij dat de deze die dit een en er het hij die in is met naar niet of
    om ook op te van voor was zijn
    """.split()
)

# The apostrophes, quotes and dashes that mean the same thing and are typed at
# random: ’ vs ' is why "van 't Klooster" pasted out of a mail client does not
# match the same name typed by hand.
_PUNCT_EQUIV = {
    "‘": "'", "’": "'", "‛": "'", "ʼ": "'", "´": "'",
    "“": '"', "”": '"', "„": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    " ": " ",
}

_WS_RE = re.compile(r"\s+")
# Punctuation stripped from the edges of a bare word. Kept *inside* it, so
# ``rutger@pantalytics.com`` and ``e-mail`` survive as one term.
_EDGE_PUNCT = "\"'`.,;:!?()[]{}<>«»…"

_TOKEN_RE = re.compile(
    r"""
    (?P<neg>-)?                      # -term  ->  must not appear
    (?:(?P<field>[a-zA-Z]+):)?       # from:  ->  scoped to a field
    (?:"(?P<phrase>[^"]*)"|(?P<word>\S+))
    """,
    re.VERBOSE,
)


def fold(text: str) -> str:
    """Casefold, strip accents, unify punctuation, collapse whitespace.

    The one normalisation both sides of every comparison go through. ``José``
    and ``jose``, ``van 't`` and ``van ’t``, ``Iris  van`` and ``Iris van`` all
    land on the same string -- which is the difference between finding a name in
    a mail signature and not.
    """
    text = "".join(_PUNCT_EQUIV.get(ch, ch) for ch in text)
    # NFKD splits an accented letter into letter + combining mark; dropping the
    # marks (category Mn) is what makes é and e the same character.
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WS_RE.sub(" ", stripped.casefold()).strip()


# An ASCII prefix shorter than this is not a search, it is a folder listing:
# ``München`` would leave as ``M``. Such a term goes out as typed instead.
_MIN_PREFIX_LEN = 3


def _ascii_prefix(value: str) -> str:
    """The leading run of a term that a backend can be asked for verbatim.

    A server matches the text in the mailbox; it will not fold ``José`` down to
    ``jose`` for us, and neither spelling finds the other. Truncating at the
    first character we cannot send faithfully (``Jos``) asks for something
    strictly *broader* than the term -- it finds both spellings, plus a
    ``Josef`` that verification then takes back out for any field a summary can
    prove. Sending one spelling would be narrower than what was asked for, and
    would miss in silence, which is the failure this module exists to end.
    """
    text = _WS_RE.sub(" ", value).strip()
    out: List[str] = []
    for ch in text:
        if ord(ch) < 128 and ch not in _PUNCT_EQUIV:
            out.append(ch)
        else:
            break
    prefix = "".join(out).strip(_EDGE_PUNCT)
    return prefix if len(prefix) >= _MIN_PREFIX_LEN else text


@dataclass(frozen=True)
class Term:
    """One thing a message has to (or must not) contain."""

    value: str
    field: str = "text"
    phrase: bool = False
    negated: bool = False

    @property
    def folded(self) -> str:
        return fold(self.value)

    @property
    def wire(self) -> str:
        """What to put on the wire for this term.

        A phrase goes out as typed with its whitespace collapsed -- a backend
        that indexes words (Graph, an IMAP server with full-text search) honours
        it, and one matching raw bytes gets the best literal available. A bare
        term goes out ASCII-broadened, because a term is verified here and a
        phrase over the body cannot be.
        """
        if self.phrase:
            return _WS_RE.sub(" ", self.value).strip()
        return _ascii_prefix(self.value)

    @property
    def distinctive(self) -> int:
        """How reluctantly this term is given up when a search has to widen.

        A stopword goes first, then the shortest -- ``van`` before ``Klooster``,
        so the term that identifies the message is the last one standing.
        """
        if self.folded in _STOPWORDS:
            return 0
        return len(self.folded)


@dataclass(frozen=True)
class Clause:
    """Terms ORed together. A plain term is a clause of one.

    Clauses AND with each other, so ``a b`` is two clauses and
    ``a OR b`` is one -- flat, two levels deep, and exactly what IMAP's
    two-argument ``OR`` and Graph's KQL both express without parentheses.
    """

    terms: Tuple[Term, ...]

    @property
    def negated(self) -> bool:
        return all(t.negated for t in self.terms)

    @property
    def distinctive(self) -> int:
        return min(t.distinctive for t in self.terms)


@dataclass(frozen=True)
class MailQuery:
    """A parsed ``mail_search`` query, ready for any backend to compile."""

    raw: str
    clauses: Tuple[Clause, ...] = ()
    has_attachment: Optional[bool] = None

    def __bool__(self) -> bool:
        return bool(self.clauses) or self.has_attachment is not None

    @property
    def terms(self) -> Tuple[Term, ...]:
        return tuple(t for c in self.clauses for t in c.terms)

    @property
    def wanted(self) -> Tuple[Clause, ...]:
        """The clauses a message must match (negations excluded)."""
        return tuple(c for c in self.clauses if not c.negated)

    def describe(self) -> List[str]:
        """The terms actually being required, for reporting back to the caller."""
        out: List[str] = []
        for clause in self.clauses:
            rendered = " OR ".join(
                f"{'-' if t.negated else ''}"
                f"{'' if t.field == 'text' else t.field + ':'}"
                f"{t.value}"
                for t in clause.terms
            )
            out.append(rendered)
        if self.has_attachment is not None:
            out.append(f"has:{'attachment' if self.has_attachment else 'no-attachment'}")
        return out


def tokenize(text: str) -> List[str]:
    """Split free text into terms worth sending, edge punctuation removed.

    ``van 't Klooster`` -> ``['van', 'Klooster']``: the ``'t`` reduces to a
    single character and is dropped rather than shipped as a ``TEXT "t"`` that
    matches the whole mailbox.
    """
    out: List[str] = []
    for raw in text.split():
        word = raw.strip(_EDGE_PUNCT)
        if len(fold(word)) >= MIN_TERM_LEN:
            out.append(word)
    return out


def parse(query: Optional[str]) -> MailQuery:
    """Parse a query string. An unparseable one still searches, never raises.

    Search is the tool a model reaches for first and a syntax error it cannot
    see is a dead end, so anything that is not recognised syntax is treated as
    an ordinary term. ``subject:`` with nothing after it is a word, not an
    error.
    """
    raw = query or ""
    if not raw.strip():
        return MailQuery(raw=raw)

    clauses: List[Clause] = []
    has_attachment: Optional[bool] = None
    pending_or = False

    for match in _TOKEN_RE.finditer(raw):
        phrase_text, word = match.group("phrase"), match.group("word")
        field = (match.group("field") or "text").lower()
        negated = bool(match.group("neg"))

        if word and word.upper() == "OR" and field == "text" and not negated:
            # Joins the clause just parsed to the one coming next. A leading or
            # trailing OR has nothing to join and is dropped.
            pending_or = bool(clauses)
            continue

        if field == "has":
            wanted = fold(word or phrase_text or "")
            if wanted in ("attachment", "attachments", "file", "true"):
                has_attachment = not negated
            elif wanted in ("no-attachment", "none", "false"):
                has_attachment = negated
            continue

        if field not in FIELDS:
            # Not a field we serve -- ``re:`` in a pasted subject line, a bare
            # ``https://...``. Search for it as written rather than dropping it.
            field, phrase_text, word = "text", phrase_text, match.group(0)

        if phrase_text is not None:
            text = _WS_RE.sub(" ", phrase_text).strip()
            if not text:
                continue
            new = [Term(value=text, field=field, phrase=True, negated=negated)]
        else:
            # The heart of it: an unquoted run of words is ANDed terms, not one
            # literal. Only a scoped term keeps its words together, because
            # ``from:van der Berg`` scopes just the first word and the rest is
            # ordinary text -- the same as every mail client.
            new = [
                Term(value=tok, field=field, negated=negated)
                for tok in tokenize(word or "")
            ]
        if not new:
            continue

        if pending_or and clauses:
            merged = clauses[-1].terms + tuple(new)
            clauses[-1] = Clause(terms=merged)
        else:
            clauses.extend(Clause(terms=(term,)) for term in new)
        pending_or = False

    return MailQuery(
        raw=raw, clauses=tuple(clauses), has_attachment=has_attachment
    )


def _without_phrases(query: MailQuery) -> Optional[Tuple[MailQuery, List[str]]]:
    """Every phrase loosened into its words. ``None`` if there were none.

    The first thing to try, because a phrase fails for reasons its words do
    not: a header fold between them, a doubled space, a comma the sender put in
    the middle.
    """
    clauses: List[Clause] = []
    given_up: List[str] = []
    for clause in query.clauses:
        if len(clause.terms) == 1 and clause.terms[0].phrase:
            term = clause.terms[0]
            words = tokenize(term.value)
            if len(words) > 1:
                clauses.extend(
                    Clause((replace(term, value=w, phrase=False),)) for w in words
                )
                given_up.append(f'"{term.value}" as one phrase')
                continue
        clauses.append(clause)
    if not given_up:
        return None
    return replace(query, clauses=tuple(clauses)), given_up


def widen(query: MailQuery) -> Iterator[Tuple[MailQuery, List[str]]]:
    """Progressively weaker versions of the query, best first.

    A search that matches nothing is worth one more question: was it *all* of
    those words that were missing, or one of them? These are the ways of
    asking, in the order worth asking them.

    The ordering is the whole design, and it is not the obvious one. It is
    tempting to give up the least distinctive term -- drop ``van``, keep
    ``Klooster`` -- and stop there. But the term that is actually absent from
    the mailbox is usually the *rarest* one, so that single guess tends to drop
    the word that was present and keep the word that was not, and the search
    still comes back empty. So each term is given up **in turn**, least
    distinctive first: the first attempt that finds something both answers the
    question and keeps as much of the query as possible.

    What is never given up is an exclusion. Dropping ``-newsletter`` would
    *add* exactly the results the caller ruled out, which is not a wider
    search, it is a different one.

    ORing the terms together is deliberately not on this ladder. It is the one
    relaxation that always "works" and never informs: for any real mailbox it
    answers a two-word question with a few thousand messages containing one
    common word, ranked by nothing.
    """
    loosened = _without_phrases(query)
    if loosened is not None:
        yield loosened
        query, prior = loosened
    else:
        prior = []

    droppable = [c for c in query.clauses if not c.negated]
    if len(droppable) <= 1:
        # Down to the last thing actually being asked for. Widening past it
        # would return the folder, which is not an answer to anything.
        return

    weakest_first = sorted(droppable, key=lambda c: (c.distinctive, len(c.terms)))
    for victim in weakest_first:
        clauses = tuple(c for c in query.clauses if c is not victim)
        yield replace(query, clauses=clauses), prior + [
            t.value for t in victim.terms
        ]

    if len(droppable) > 2:
        # Nothing survived losing one term, so ask for the single term most
        # likely to identify the message and let the caller see what came back.
        strongest = weakest_first[-1]
        kept = tuple(c for c in query.clauses if c is strongest or c.negated)
        yield replace(query, clauses=kept), prior + [
            t.value for c in weakest_first[:-1] for t in c.terms
        ]


def _haystacks(summary, field: str) -> List[str]:
    """The text of one summary field, as strings to fold and match against."""
    if field == "from":
        return [str(summary.from_addr or "")]
    if field == "to":
        return [str(a) for a in (summary.to_addrs or [])]
    if field == "subject":
        return [str(summary.subject or "")]
    return []


def _term_hits(term: Term, summary) -> bool:
    needle = term.folded
    return any(needle in fold(h) for h in _haystacks(summary, term.field))


def verify(query: MailQuery, summaries: Sequence) -> List:
    """Drop summaries that provably do not match, keep everything else.

    This is the half of the contract that makes two backends answer the same
    way. The server was asked something at least as broad as the query -- an
    ASCII-broadened term, a KQL keyword that ignores order, an IMAP server with
    its own idea of what ``TEXT`` means -- and here that breadth is taken back
    for every field a summary can prove: sender, recipients, subject, and
    whether there is a file attached.

    A field a summary does *not* carry is left alone on purpose. The preview is
    a couple of hundred characters of a body that may be megabytes, so a
    free-text or ``body:`` term that is absent from it says nothing at all, and
    filtering on it would throw away the matches this whole module exists to
    find.
    """
    if not query:
        return list(summaries)

    kept = []
    for summary in summaries:
        if query.has_attachment is not None:
            if bool(getattr(summary, "has_attachments", False)) != query.has_attachment:
                continue
        if any(
            _clause_refutes(clause, summary) for clause in query.clauses
        ):
            continue
        kept.append(summary)
    return kept


def _clause_refutes(clause: Clause, summary) -> bool:
    """True when this summary can be *shown* to fail the clause."""
    checkable = [t for t in clause.terms if t.field in VERIFIABLE]
    if len(checkable) != len(clause.terms):
        # Part of the clause reaches somewhere a summary cannot see (the body,
        # cc). An OR clause is satisfiable by that part, so it proves nothing.
        return False
    if clause.negated:
        # Every term is a "must not", and they were ANDed by the parser: the
        # message fails only if one of them is actually present.
        return any(_term_hits(t, summary) for t in clause.terms)
    return not any(_term_hits(t, summary) for t in clause.terms)
