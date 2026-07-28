"""Where a CardDAV URL leads, without a server.

CardDAV standardises no paths, so the URL a provider publishes is whatever that
provider felt like publishing: a bare host name, a discovery root, or the
address-book home itself. This backend used to assume one shape -- ``<url>`` +
``/addressbooks/`` -- which is why a customer could paste the address their
provider documents and still be told there were no address books.

Discovery is what makes a *prefilled* CardDAV URL possible, so the shapes it has
to swallow are worth pinning on every branch push. The Radicale e2e in the admin
package proves the happy path against a real server; this proves the three
shapes, the fallback, and that a 401 stays a 401 instead of being retried into a
confusing "nothing found".
"""

from __future__ import annotations

import httpx
import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import ProviderAuthError, ProviderError
from squirrel_mcp.providers.soverin import contacts as contacts_module
from squirrel_mcp.providers.soverin.contacts import SoverinContactsProvider

MULTISTATUS = "application/xml; charset=utf-8"

PRINCIPAL_BODY = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:"><d:response>
  <d:href>/dav/</d:href>
  <d:propstat><d:prop>
    <d:current-user-principal><d:href>/dav/principals/rutger/</d:href></d:current-user-principal>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
</d:response></d:multistatus>"""

HOME_BODY = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"><d:response>
  <d:href>/dav/principals/rutger/</d:href>
  <d:propstat><d:prop>
    <card:addressbook-home-set><d:href>/dav/books/rutger/</d:href></card:addressbook-home-set>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
</d:response></d:multistatus>"""

BOOKS_BODY = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"><d:response>
  <d:href>/dav/books/rutger/default/</d:href>
  <d:propstat><d:prop>
    <d:resourcetype><d:collection/><card:addressbook/></d:resourcetype>
    <d:displayname>Contacts</d:displayname>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
</d:response></d:multistatus>"""


def build(url: str, handler) -> SoverinContactsProvider:
    """A provider whose httpx client answers from ``handler`` instead of a network."""
    config = SquirrelConfig(
        mail_email="rutger@example.com",
        mail_password="secret",
        carddav_url=url,
        skip_validation=True,
    )
    provider = SoverinContactsProvider(config)
    real_client = httpx.Client
    contacts_module.httpx.Client = lambda **kw: real_client(
        **kw, transport=httpx.MockTransport(handler)
    )
    return provider


@pytest.fixture(autouse=True)
def restore_httpx():
    original = contacts_module.httpx.Client
    yield
    contacts_module.httpx.Client = original


def multistatus(body: str) -> httpx.Response:
    return httpx.Response(207, content=body, headers={"Content-Type": MULTISTATUS})


def test_a_bare_host_name_is_followed_to_the_address_book_home():
    """What iCloud, Yandex and GMX publish is a host, and nothing else."""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/.well-known/carddav":
            return multistatus(PRINCIPAL_BODY)
        if request.url.path == "/dav/principals/rutger/":
            return multistatus(HOME_BODY)
        if request.url.path == "/dav/books/rutger/":
            return multistatus(BOOKS_BODY)
        return httpx.Response(404)

    provider = build("https://dav.example.com", handler)
    books = provider.list_addressbooks()

    assert "/.well-known/carddav" in seen
    assert provider._books_url == "https://dav.example.com/dav/books/rutger/"
    assert [b.name for b in books] == ["Contacts"]


def test_the_home_collection_itself_is_accepted_as_the_url():
    """Fastmail documents the home; discovery from it must not walk past it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/dav/books/rutger/":
            body = PRINCIPAL_BODY if b"current-user-principal" in request.content else BOOKS_BODY
            return multistatus(body)
        if request.url.path == "/dav/principals/rutger/":
            return multistatus(HOME_BODY)
        return httpx.Response(404)

    provider = build("https://dav.example.com/dav/books/rutger/", handler)

    assert [b.name for b in provider.list_addressbooks()] == ["Contacts"]


def test_a_server_without_discovery_still_gets_the_old_convention():
    """The layout this backend assumed before it could discover one."""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if b"current-user-principal" in request.content:
            return httpx.Response(404)  # no discovery on this server
        if request.url.path == "/addressbooks/":
            return multistatus(BOOKS_BODY)
        return httpx.Response(404)

    provider = build("https://old.example.com", handler)
    provider.connect()

    assert provider._books_url == "https://old.example.com/addressbooks/"


def test_a_wrong_password_is_reported_as_one():
    """Not as "no address books found" three URLs later."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    provider = build("https://dav.example.com", handler)

    with pytest.raises(ProviderAuthError):
        provider.connect()


def test_a_url_with_no_books_anywhere_says_so():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider = build("https://nothing.example.com", handler)

    with pytest.raises(ProviderError, match="No CardDAV address books"):
        provider.connect()


def test_an_unreachable_server_is_a_provider_error():
    """httpx's own transport error would otherwise escape the protocol layer."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    provider = build("https://down.example.com", handler)

    with pytest.raises(ProviderError, match="Cannot reach CardDAV server"):
        provider.connect()
