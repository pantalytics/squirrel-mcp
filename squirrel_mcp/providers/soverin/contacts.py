"""Contacts (CardDAV) backend: raw CardDAV over httpx + ``vobject`` for vCards.

There is no dominant modern CardDAV client library, so this speaks CardDAV
directly (PROPFIND to list books, addressbook-query REPORT to read cards, PUT/
DELETE to write) and leans on ``vobject`` for vCard parsing/serialising. Only this
module knows CardDAV; it returns the neutral dataclasses from ``providers.protocol``.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
import vobject

from ...config import SquirrelConfig
from ...logging_config import get_logger
from ..protocol import (
    AddressBookInfo,
    ContactDetail,
    ContactSummary,
    ProviderAuthError,
    ProviderError,
    ProviderNotFoundError,
)

logger = get_logger(__name__)

NS = {"d": "DAV:", "card": "urn:ietf:params:xml:ns:carddav"}

_PROPFIND_BOOKS = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop><d:resourcetype/><d:displayname/></d:prop>
</d:propfind>"""

_REPORT_CARDS = """<?xml version="1.0" encoding="utf-8"?>
<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">
  <d:prop><d:getetag/><card:address-data/></d:prop>
</card:addressbook-query>"""


class SoverinContactsProvider:
    """ContactsProvider backed by CardDAV (Soverin / any SabreDAV-style server)."""

    def __init__(self, config: SquirrelConfig):
        base = config.carddav_url.rstrip("/")
        self._origin = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
        # Soverin exposes address books under /addressbooks/. (Principal discovery
        # is a later refinement for other servers.)
        self._books_url = base + "/addressbooks/"
        self._auth = (config.login_username, config.mail_password)
        self._verify = config.tls_verify
        self._client: Optional[httpx.Client] = None

    @property
    def is_authenticated(self) -> bool:
        return self._client is not None

    def connect(self) -> None:
        self._client = httpx.Client(
            auth=self._auth, verify=self._verify, timeout=30, follow_redirects=True
        )
        # Validate credentials with a cheap PROPFIND.
        self._propfind(self._books_url, depth=0)

    def authenticate(self) -> None:
        if self._client is None:
            self.connect()

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _abs(self, href: str) -> str:
        return href if href.startswith("http") else urljoin(self._origin, href)

    def _propfind(self, url: str, depth: int) -> ET.Element:
        self.authenticate()
        r = self._client.request(
            "PROPFIND", url,
            headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"},
            content=_PROPFIND_BOOKS,
        )
        if r.status_code == 401:
            raise ProviderAuthError("CardDAV login failed (401)")
        if r.status_code == 404:
            raise ProviderNotFoundError(f"Not found: {url}")
        if r.status_code != 207:
            raise ProviderError(f"PROPFIND {url} returned HTTP {r.status_code}")
        return ET.fromstring(r.content)

    def _report_cards(self, book_url: str) -> List[Tuple[str, str]]:
        """Return [(href, vcard_text)] for every card in an address book."""
        self.authenticate()
        r = self._client.request(
            "REPORT", book_url,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
            content=_REPORT_CARDS,
        )
        if r.status_code == 401:
            raise ProviderAuthError("CardDAV login failed (401)")
        if r.status_code not in (207, 200):
            raise ProviderError(f"REPORT {book_url} returned HTTP {r.status_code}")
        root = ET.fromstring(r.content)
        out = []
        for resp in root.findall("d:response", NS):
            href = resp.findtext("d:href", default="", namespaces=NS)
            data = resp.findtext(".//card:address-data", default="", namespaces=NS)
            if href and data and href.lower().endswith(".vcf"):
                out.append((href, data))
        return out

    # ---- reads ----------------------------------------------------------- #
    def list_addressbooks(self) -> List[AddressBookInfo]:
        root = self._propfind(self._books_url, depth=1)
        books = []
        for resp in root.findall("d:response", NS):
            if resp.find(".//card:addressbook", NS) is None:
                continue
            href = resp.findtext("d:href", default="", namespaces=NS)
            name = resp.findtext(".//d:displayname", default="", namespaces=NS)
            books.append(AddressBookInfo(id=self._abs(href), name=name or "(unnamed)"))
        return books

    def search_contacts(
        self,
        addressbook: str,
        *,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ContactSummary], int]:
        cards = self._report_cards(self._abs(addressbook))
        summaries = []
        for _href, text in cards:
            try:
                summaries.append(self._to_summary(text, addressbook))
            except Exception:  # noqa: BLE001 - skip unparseable cards
                continue
        if query:
            q = query.lower()
            summaries = [
                c for c in summaries
                if q in c.full_name.lower()
                or any(q in e.lower() for e in c.emails)
                or (c.organization and q in c.organization.lower())
            ]
        summaries.sort(key=lambda c: c.full_name.lower())
        total = len(summaries)
        return summaries[offset : offset + limit], total

    def get_contact(self, addressbook: str, uid: str) -> ContactDetail:
        _, text = self._find_card(addressbook, uid)
        card = vobject.readOne(text)
        return self._to_detail(card, addressbook)

    # ---- writes ---------------------------------------------------------- #
    def create_contact(
        self,
        addressbook: str,
        full_name: str,
        *,
        emails: Optional[List[str]] = None,
        phones: Optional[List[str]] = None,
        organization: Optional[str] = None,
    ) -> str:
        uid = str(uuid.uuid4())
        card = vobject.vCard()
        card.add("uid").value = uid
        card.add("fn").value = full_name
        n = card.add("n")
        parts = full_name.split(" ", 1)
        n.value = vobject.vcard.Name(
            family=parts[1] if len(parts) > 1 else "", given=parts[0]
        )
        for em in emails or []:
            e = card.add("email")
            e.value = em
            e.type_param = "INTERNET"
        for ph in phones or []:
            card.add("tel").value = ph
        if organization:
            card.add("org").value = [organization]

        href = urljoin(self._abs(addressbook).rstrip("/") + "/", f"{uid}.vcf")
        self.authenticate()
        r = self._client.put(
            href,
            content=card.serialize(),
            headers={"Content-Type": "text/vcard; charset=utf-8", "If-None-Match": "*"},
        )
        if r.status_code not in (200, 201, 204):
            raise ProviderError(f"Create contact failed: HTTP {r.status_code}")
        return uid

    def update_contact(
        self,
        addressbook: str,
        uid: str,
        *,
        full_name: Optional[str] = None,
        emails: Optional[List[str]] = None,
        phones: Optional[List[str]] = None,
        organization: Optional[str] = None,
    ) -> str:
        href, text = self._find_card(addressbook, uid)
        card = vobject.readOne(text)
        if full_name is not None:
            card.fn.value = full_name
        if emails is not None:
            for e in list(card.contents.get("email", [])):
                card.remove(e)
            for em in emails:
                e = card.add("email")
                e.value = em
                e.type_param = "INTERNET"
        if phones is not None:
            for t in list(card.contents.get("tel", [])):
                card.remove(t)
            for ph in phones:
                card.add("tel").value = ph
        if organization is not None:
            if hasattr(card, "org"):
                card.org.value = [organization]
            else:
                card.add("org").value = [organization]
        self.authenticate()
        r = self._client.put(
            self._abs(href),
            content=card.serialize(),
            headers={"Content-Type": "text/vcard; charset=utf-8"},
        )
        if r.status_code not in (200, 201, 204):
            raise ProviderError(f"Update contact failed: HTTP {r.status_code}")
        return uid

    def delete_contact(self, addressbook: str, uid: str) -> None:
        href, _ = self._find_card(addressbook, uid)
        self.authenticate()
        r = self._client.delete(self._abs(href))
        if r.status_code not in (200, 204):
            raise ProviderError(f"Delete contact failed: HTTP {r.status_code}")

    # ---- helpers --------------------------------------------------------- #
    def _find_card(self, addressbook: str, uid: str) -> Tuple[str, str]:
        for href, text in self._report_cards(self._abs(addressbook)):
            try:
                card = vobject.readOne(text)
            except Exception:  # noqa: BLE001
                continue
            if getattr(getattr(card, "uid", None), "value", None) == uid:
                return href, text
        raise ProviderNotFoundError(f"Contact {uid} not found in {addressbook}")

    @classmethod
    def _to_summary(cls, text: str, addressbook: str) -> ContactSummary:
        card = vobject.readOne(text)
        return ContactSummary(
            uid=getattr(getattr(card, "uid", None), "value", "") or "",
            addressbook=addressbook,
            full_name=getattr(getattr(card, "fn", None), "value", "") or "",
            emails=[e.value for e in card.contents.get("email", [])],
            phones=[t.value for t in card.contents.get("tel", [])],
            organization=cls._org(card),
        )

    @classmethod
    def _to_detail(cls, card, addressbook: str) -> ContactDetail:
        return ContactDetail(
            uid=getattr(getattr(card, "uid", None), "value", "") or "",
            addressbook=addressbook,
            full_name=getattr(getattr(card, "fn", None), "value", "") or "",
            emails=[e.value for e in card.contents.get("email", [])],
            phones=[t.value for t in card.contents.get("tel", [])],
            organization=cls._org(card),
            title=getattr(getattr(card, "title", None), "value", None),
            note=getattr(getattr(card, "note", None), "value", None),
        )

    @staticmethod
    def _org(card) -> Optional[str]:
        org = getattr(card, "org", None)
        if org is None:
            return None
        val = org.value
        if isinstance(val, list):
            return ", ".join(str(v) for v in val if v)
        return str(val)
