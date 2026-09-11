"""Postal addresses on a contact: vCard ``ADR`` through the CardDAV backend.

A contact's address lives in the vCard on the server, and until now the tools
could neither read nor write it. The shape is vCard's own -- seven components
in fixed order, ``po_box;extended;street;city;region;postal_code;country`` --
and it has to survive three things a fake cannot fake by accident: the 3.0 and
4.0 param dialects, the escaping of ``;`` ``,`` and ``\\`` inside a component,
and the read-modify-write rule that an update keeps every property it does not
understand. The Soverin acceptance run proves it against a real server; this
pins the seams on every branch push.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import httpx
import pytest
import vobject
from pydantic import ValidationError as PydanticValidationError

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.error_handling import ValidationError
from squirrel_mcp.providers.protocol import ContactAddress, ProviderError
from squirrel_mcp.providers.soverin.contacts import SoverinContactsProvider
from squirrel_mcp.tools.contacts import _addresses_in

BOOK = "https://dav.example.com/addressbooks/rutger/default/"
UID = "9ccb6966-bce1-54da-b463-e550c72fe0d1"

CARD_30 = f"""BEGIN:VCARD
VERSION:3.0
UID:{UID}
FN:Bastiaan Bakker
N:Bakker;Bastiaan;;;
EMAIL;TYPE=INTERNET:bastiaan_bakker88@hotmail.com
TEL;TYPE=CELL:+31650597053
ADR;TYPE=HOME:;Flat 1;Jura 28;Almelo;;7607 RG;Netherlands
LABEL;TYPE=HOME:Jura 28\\nAlmelo
ADR;TYPE=WORK,PREF:PO 12;;Rue de l'Enseignement 48\\, bus 2;Brussels;Brussels-Capital;1000;Belgium
NOTE:Met at the conference\\, 2024
CATEGORIES:Friends,Work
PHOTO;ENCODING=b;TYPE=JPEG:/9j/4AAQSkZJRg==
X-ABUID:ABC-123
END:VCARD
"""

CARD_40 = f"""BEGIN:VCARD
VERSION:4.0
UID:{UID}
FN:Bastiaan Bakker
N:Bakker;Bastiaan;;;
EMAIL:bastiaan_bakker88@hotmail.com
TEL;VALUE=uri:tel:+31650597053
ADR;TYPE=home;PREF=1:;;Jura 28;Almelo;;7607 RG;Netherlands
ADR;TYPE=work:;;Rue de l'Enseignement 48;Brussels;;1000;Belgium
X-ABUID:ABC-123
END:VCARD
"""


class FakeCardDav:
    """One address book with one card, answering REPORT and PUT like a server.

    Keeps the ETag it hands out and the ``If-Match`` it gets back, which is the
    whole point: an update that does not echo the ETag overwrites whatever
    another client saved in between.
    """

    def __init__(self, card: str, etag: str = '"etag-1"'):
        self.card = card
        self.etag = etag
        self.puts: List[Tuple[Dict[str, str], str]] = []
        self.deletes: List[Dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "REPORT":
            body = f"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"><d:response>
  <d:href>/addressbooks/rutger/default/{UID}.vcf</d:href>
  <d:propstat><d:prop>
    <d:getetag>{self.etag}</d:getetag>
    <card:address-data><![CDATA[{self.card}]]></card:address-data>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
</d:response></d:multistatus>"""
            return httpx.Response(207, content=body)
        if request.method == "PUT":
            self.puts.append((dict(request.headers), request.content.decode()))
            if request.headers.get("If-Match", self.etag) != self.etag:
                return httpx.Response(412)
            self.card = request.content.decode()
            return httpx.Response(204)
        if request.method == "DELETE":
            self.deletes.append(dict(request.headers))
            return httpx.Response(204)
        return httpx.Response(404)


def build(server: FakeCardDav) -> SoverinContactsProvider:
    config = SquirrelConfig(
        mail_email="rutger@example.com",
        mail_password="secret",
        carddav_url="https://dav.example.com",
        skip_validation=True,
    )
    provider = SoverinContactsProvider(config)
    # Skip discovery: the book is addressed absolutely, and this is about cards.
    provider._client = httpx.Client(transport=httpx.MockTransport(server))
    provider._books_url = "https://dav.example.com/addressbooks/rutger/"
    return provider


def adr_lines(vcard: str) -> List[str]:
    """The ADR lines of a serialized card, folds undone."""
    unfolded = vcard.replace("\r\n ", "").replace("\n ", "")
    return [ln for ln in unfolded.splitlines() if ln.startswith("ADR")]


# ---- reading --------------------------------------------------------------- #


def test_a_vcard_30_with_two_addresses_reads_component_by_component():
    detail = build(FakeCardDav(CARD_30)).get_contact(BOOK, UID)

    assert detail.addresses == [
        ContactAddress(
            type="home", extended="Flat 1", street="Jura 28", city="Almelo",
            postal_code="7607 RG", country="Netherlands",
        ),
        ContactAddress(
            type="work", po_box="PO 12", street="Rue de l'Enseignement 48, bus 2",
            city="Brussels", region="Brussels-Capital", postal_code="1000",
            country="Belgium", preferred=True,
        ),
    ]
    # The rest of the card is untouched by the new field.
    assert detail.emails == ["bastiaan_bakker88@hotmail.com"]
    assert detail.phones == ["+31650597053"]
    assert detail.note == "Met at the conference, 2024"


def test_a_vcard_40_reads_the_lowercase_type_and_pref_param():
    detail = build(FakeCardDav(CARD_40)).get_contact(BOOK, UID)

    assert [(a.type, a.preferred, a.street) for a in detail.addresses] == [
        ("home", True, "Jura 28"),
        ("work", False, "Rue de l'Enseignement 48"),
    ]


def test_a_contact_without_addresses_reads_an_empty_list_not_none():
    bare = "BEGIN:VCARD\nVERSION:3.0\nUID:%s\nFN:X\nN:X;;;;\nEND:VCARD\n" % UID
    assert build(FakeCardDav(bare)).get_contact(BOOK, UID).addresses == []


# ---- writing --------------------------------------------------------------- #


def test_an_update_without_addresses_leaves_the_card_as_parsed():
    """Read-modify-write: what goes back is the whole card, ADR and all.

    The PUT body is exactly what ``vobject`` makes of the card it read, so
    nothing the tool does not model -- the second ADR, the LABEL, PHOTO,
    CATEGORIES, X-* -- can go missing on the way through.
    """
    server = FakeCardDav(CARD_30)
    build(server).update_contact(BOOK, UID, organization="Pantalytics")

    (_headers, body), = server.puts
    expected = vobject.readOne(CARD_30)
    expected.add("org").value = ["Pantalytics"]
    assert body == expected.serialize()
    for prop in ("PHOTO;", "CATEGORIES:Friends,Work", "X-ABUID:ABC-123", "LABEL;TYPE=HOME"):
        assert prop in body
    assert adr_lines(body) == adr_lines(CARD_30)


def test_commas_and_semicolons_in_a_street_are_escaped_and_read_back():
    server = FakeCardDav(CARD_30)
    provider = build(server)
    provider.update_contact(BOOK, UID, addresses=[
        ContactAddress(street="Kerkstraat 1; 2, rear", city="A, B", postal_code="1234 AB"),
    ])

    (_headers, body), = server.puts
    assert adr_lines(body) == [r"ADR;TYPE=HOME:;;Kerkstraat 1\; 2\, rear;A\, B;;1234 AB;"]
    back = provider.get_contact(BOOK, UID).addresses
    assert back == [ContactAddress(street="Kerkstraat 1; 2, rear", city="A, B", postal_code="1234 AB")]


def test_writing_to_a_30_card_speaks_30_and_to_a_40_card_speaks_40():
    addresses = [
        ContactAddress(type="home", street="Jura 28", city="Almelo"),
        ContactAddress(type="work", street="Hoogstraat 109u", city="Rotterdam", preferred=True),
    ]
    s30 = FakeCardDav(CARD_30)
    build(s30).update_contact(BOOK, UID, addresses=addresses)
    assert "VERSION:3.0" in s30.card
    assert adr_lines(s30.card) == [
        "ADR;TYPE=HOME:;;Jura 28;Almelo;;;",
        "ADR;TYPE=WORK,PREF:;;Hoogstraat 109u;Rotterdam;;;",
    ]
    # The LABEL described the address that was just replaced.
    assert "LABEL" not in s30.card

    s40 = FakeCardDav(CARD_40)
    build(s40).update_contact(BOOK, UID, addresses=addresses)
    assert "VERSION:4.0" in s40.card
    assert adr_lines(s40.card) == [
        "ADR;TYPE=home:;;Jura 28;Almelo;;;",
        "ADR;PREF=1;TYPE=work:;;Hoogstraat 109u;Rotterdam;;;",
    ]


def test_an_empty_list_clears_every_address_and_nothing_else():
    server = FakeCardDav(CARD_30)
    provider = build(server)
    provider.update_contact(BOOK, UID, addresses=[])

    assert adr_lines(server.card) == []
    detail = provider.get_contact(BOOK, UID)
    assert detail.addresses == []
    assert detail.emails == ["bastiaan_bakker88@hotmail.com"]
    assert detail.phones == ["+31650597053"]
    assert "X-ABUID:ABC-123" in server.card


def test_create_writes_the_addresses_it_is_given():
    server = FakeCardDav(CARD_30)
    provider = build(server)
    provider.create_contact(BOOK, "New Person", addresses=[
        ContactAddress(street="Jura 28", city="Almelo", country="Netherlands"),
    ])

    (headers, body), = server.puts
    assert headers["if-none-match"] == "*"
    assert adr_lines(body) == ["ADR;TYPE=HOME:;;Jura 28;Almelo;;;Netherlands"]


# ---- concurrency ----------------------------------------------------------- #


def test_the_update_echoes_the_etag_it_read_and_a_412_is_a_clear_error():
    server = FakeCardDav(CARD_30, etag='"etag-7"')
    provider = build(server)
    provider.update_contact(BOOK, UID, full_name="Bastiaan")
    (headers, _body), = server.puts
    assert headers["if-match"] == '"etag-7"'

    # Someone else saved in between: the server's ETag moved under us.
    server.etag = '"etag-8"'
    server.card = CARD_30
    # The provider re-reads (and so sees etag-8) -- simulate the race by
    # answering the PUT as if the ETag had moved after the REPORT.
    original = server.__call__

    def racing(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(412)
        return original(request)

    provider._client = httpx.Client(transport=httpx.MockTransport(racing))
    with pytest.raises(ProviderError, match="changed on the server"):
        provider.update_contact(BOOK, UID, full_name="Bastiaan")


def test_delete_echoes_the_etag_too():
    server = FakeCardDav(CARD_30, etag='"etag-3"')
    build(server).delete_contact(BOOK, UID)
    assert server.deletes[0]["if-match"] == '"etag-3"'


# ---- the tool argument ----------------------------------------------------- #


def test_the_tool_takes_objects_only_and_refuses_an_empty_one():
    from squirrel_mcp.schemas import ContactAddressOut

    out = _addresses_in([ContactAddressOut(street=" Jura 28 ", city="Almelo")])
    assert out == [ContactAddress(street="Jura 28", city="Almelo")]

    with pytest.raises(ValidationError, match=r"addresses\[0\] is empty"):
        _addresses_in([ContactAddressOut(type="work")])
    # A flat string never reaches the provider: Pydantic refuses it at the
    # schema, which is what keeps "Jura 28, Almelo" from being guessed apart.
    with pytest.raises(PydanticValidationError):
        ContactAddressOut.model_validate("Jura 28, Almelo")
