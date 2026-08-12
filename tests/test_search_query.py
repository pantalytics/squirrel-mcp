"""The search grammar, the folding, and the widening ladder.

The bug these are written against: searching a mailbox for a contact's name
found nothing, because the name reached the mailbox with a second space in it
and the whole query went out as one literal. Every "finds it anyway" test below
is a shape that used to come back empty.
"""

from __future__ import annotations

import pytest

from squirrel_mcp.providers.protocol import MessageSummary
from squirrel_mcp.search_query import fold, parse, tokenize, verify, widen


def _summary(**kw) -> MessageSummary:
    base = dict(
        uid="1",
        folder="INBOX",
        subject="",
        from_addr="",
        to_addrs=[],
        date="",
        flags=[],
        size=0,
        has_attachments=False,
        preview="",
    )
    base.update(kw)
    return MessageSummary(**base)


# ---- folding: the actual reported failure ------------------------------- #
@pytest.mark.parametrize(
    "written, typed",
    [
        ("Iris  van 't Klooster", "Iris van 't Klooster"),   # doubled space
        ("Iris van ’t Klooster", "Iris van 't Klooster"),    # curly apostrophe
        ("José Núñez", "Jose Nunez"),                        # accents
        ("SCHREEUWEN", "schreeuwen"),                        # case
        ("a\r\n b", "a b"),                                  # header fold
        ("em—dash", "em-dash"),                              # dash flavours
    ],
)
def test_the_spellings_that_should_not_matter_do_not(written, typed):
    assert fold(written) == fold(typed)


def test_an_apostrophe_fragment_is_not_shipped_as_a_search_term():
    """``'t`` reduces to one character, which matches most of a mailbox."""
    assert tokenize("van 't Klooster") == ["van", "Klooster"]


def test_an_address_keeps_its_punctuation():
    assert tokenize("mail rutger@pantalytics.com, e-mail") == [
        "mail",
        "rutger@pantalytics.com",
        "e-mail",
    ]


# ---- parsing ------------------------------------------------------------- #
def test_unquoted_words_become_separate_terms_not_one_phrase():
    """The fix itself: three keys the server ANDs, not one literal to miss."""
    q = parse("Iris van 't Klooster")
    assert [t.value for t in q.terms] == ["Iris", "van", "Klooster"]
    assert not any(t.phrase for t in q.terms)


def test_quotes_are_how_you_ask_for_a_phrase():
    q = parse('"fiscal localization"')
    assert len(q.terms) == 1
    assert q.terms[0].phrase and q.terms[0].value == "fiscal localization"


def test_fields_negation_or_and_attachments_parse():
    q = parse('from:iris subject:"fiscal localization" -newsletter has:attachment')
    by_field = {(t.field, t.value): t for t in q.terms}
    assert ("from", "iris") in by_field
    assert by_field[("subject", "fiscal localization")].phrase
    assert by_field[("text", "newsletter")].negated
    assert q.has_attachment is True

    either = parse("invoice OR factuur")
    assert len(either.clauses) == 1
    assert [t.value for t in either.clauses[0].terms] == ["invoice", "factuur"]


def test_an_unknown_prefix_is_searched_for_not_dropped():
    """``Re: something`` pasted out of a subject line is a query, not a syntax
    error -- a search that silently ignores half its input is worse than one
    that looks too hard."""
    q = parse("Re:Fiscal")
    assert [t.value for t in q.terms] == ["Re:Fiscal"]


def test_junk_never_raises():
    for junk in ("", "   ", '"', '""', "-", "from:", "OR", "'", "((("):
        parse(junk)


def test_an_accented_term_leaves_broadened_never_narrowed():
    """A server will not fold ``José`` for us, and neither spelling finds the
    other -- so what goes out is the part that can be sent faithfully."""
    assert parse("José").terms[0].wire == "Jos"
    # Too short to be a search on its own: sent as typed instead.
    assert parse("Ötzi").terms[0].wire == "Ötzi"
    assert parse("klooster").terms[0].wire == "klooster"


# ---- verification: where the backends are made to agree ------------------ #
def test_a_visible_field_is_verified_exactly():
    """The breadth added on the way out is taken back for what a summary can
    prove. ``from:José`` leaves as ``Jos`` so the server returns Joshua too;
    that is ours to undo, and both spellings of the name are ours to keep.

    Note the line this draws: matching stays *substring*, the way IMAP's own
    keys match, so ``jose`` still finds ``Josef``. Verification exists to undo
    what we widened, not to invent a stricter rule for the three fields a
    summary happens to carry -- that would make a term mean one thing in the
    subject and another in the body.
    """
    accented = _summary(from_addr="José Núñez <j.nunez@example.com>")
    plain = _summary(from_addr="Jose Nunez <jose@example.com>")
    joshua = _summary(from_addr="Joshua Tree <josh@example.com>")
    assert verify(parse("from:José"), [accented, plain, joshua]) == [accented, plain]


def test_a_field_a_summary_cannot_see_is_left_to_the_server():
    """A preview is a couple of hundred characters of a body that may be huge,
    so its silence proves nothing and must not drop the message."""
    msg = _summary(subject="Re: Fiscal", preview="short preview")
    assert verify(parse("localization"), [msg]) == [msg]
    assert verify(parse("body:localization"), [msg]) == [msg]
    assert verify(parse("cc:someone"), [msg]) == [msg]


def test_negation_and_attachments_are_verified():
    plain = _summary(subject="Weekly newsletter")
    other = _summary(subject="Invoice 42", has_attachments=True)
    assert verify(parse("-subject:newsletter"), [plain, other]) == [other]
    assert verify(parse("has:attachment"), [plain, other]) == [other]


def test_an_or_clause_survives_on_its_unverifiable_half():
    """``from:x OR body:x`` cannot be refuted by a summary: the body half might
    be what matched."""
    msg = _summary(from_addr="someone@example.com")
    assert verify(parse("from:iris OR body:iris"), [msg]) == [msg]


# ---- widening ------------------------------------------------------------ #
def test_a_phrase_is_loosened_into_its_words_first():
    first, gave_up = next(widen(parse('"fiscal localization"')))
    assert [t.value for t in first.terms] == ["fiscal", "localization"]
    assert not any(t.phrase for t in first.terms)
    assert "as one phrase" in gave_up[0]


def test_the_least_distinctive_term_is_given_up_first():
    """``van`` before ``Klooster``: the word that identifies the message should
    be the last one standing, not the first one dropped."""
    first, gave_up = next(widen(parse("Iris van Klooster")))
    assert gave_up == ["van"]
    assert [t.value for t in first.terms] == ["Iris", "Klooster"]


def test_every_term_gets_a_turn_at_being_the_missing_one():
    """The reason the ladder is not a single guess. The absent word is usually
    the rarest, so dropping only the weakest keeps the word that is not there
    and the search stays empty -- each has to be tried."""
    given_up = [gave_up for _, gave_up in widen(parse("Klooster invoice quarterly"))]
    assert ["quarterly"] in given_up
    assert ["invoice"] in given_up
    assert ["Klooster"] in given_up
    # Least distinctive first, so the best answers come before the worst ones.
    assert given_up[0] == ["invoice"]


def test_widening_stops_before_it_would_return_the_folder():
    assert list(widen(parse("klooster"))) == []
    assert list(widen(parse("invoice OR factuur"))) == []


def test_widening_never_gives_up_an_exclusion():
    """Dropping ``-newsletter`` would *add* the results the caller ruled out --
    that is not a wider search, it is a different one."""
    for candidate, gave_up in widen(parse("quarterly report roundup -newsletter")):
        assert "newsletter" not in gave_up
        assert any(t.value == "newsletter" and t.negated for t in candidate.terms)


def test_the_ladder_is_finite():
    assert len(list(widen(parse('"one two" three four five')))) < 12


def test_a_mixed_or_clause_is_satisfied_by_either_half():
    """``invoice OR -newsletter``: the negated half is what keeps a message
    with neither word, so the clause refutes nothing on its own."""
    plain = _summary(subject="Weekly digest")
    named = _summary(subject="Invoice 42")
    letter = _summary(subject="Weekly newsletter")
    kept = verify(parse("subject:invoice OR -subject:newsletter"), [plain, named, letter])
    assert kept == [plain, named]


def test_excluding_two_words_at_once_excludes_both():
    """``-a OR -b`` reads as "neither", which is what both compilers emit."""
    clean = _summary(subject="Invoice 42")
    one = _summary(subject="Invoice newsletter")
    other = _summary(subject="Invoice digest")
    q = parse("-subject:newsletter OR -subject:digest")
    assert verify(q, [clean, one, other]) == [clean]
