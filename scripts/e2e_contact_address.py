#!/usr/bin/env python3
"""Acceptance run for postal addresses against your live CardDAV address book.

Reads credentials from .env (SQUIRREL_MAIL_EMAIL / _PASSWORD / SQUIRREL_CARDDAV_URL),
writes ONE address onto the given contact through the real provider, reads it
back, and prints emails, phones and note so you can see they survived.

    python scripts/e2e_contact_address.py <uid> [addressbook-url]

The addressbook defaults to the first book discovery finds. Nothing else on the
card is touched; the address stays -- check it in the provider's webmail.
"""

from __future__ import annotations

import sys

from squirrel_mcp.config import load_config
from squirrel_mcp.providers.protocol import ContactAddress
from squirrel_mcp.providers.soverin.contacts import SoverinContactsProvider

ADDRESS = ContactAddress(
    type="home",
    street="Hoogstraat 109u",
    postal_code="3011 PL",
    city="Rotterdam",
    country="Netherlands",
)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    uid = sys.argv[1]
    config = load_config()
    if not config.carddav_url:
        print("SQUIRREL_CARDDAV_URL is not set", file=sys.stderr)
        return 1

    provider = SoverinContactsProvider(config)
    provider.connect()
    try:
        book = sys.argv[2] if len(sys.argv) > 2 else provider.list_addressbooks()[0].id
        before = provider.get_contact(book, uid)
        print(f"{before.full_name}: {len(before.addresses)} address(es) before")

        provider.update_contact(book, uid, addresses=[ADDRESS])
        after = provider.get_contact(book, uid)
        print(f"addresses: {after.addresses}")
        print(f"emails:    {after.emails}  (before: {before.emails})")
        print(f"phones:    {after.phones}  (before: {before.phones})")
        print(f"note:      {after.note!r}  (before: {before.note!r})")

        ok = (
            after.addresses == [ADDRESS]
            and after.emails == before.emails
            and after.phones == before.phones
            and after.note == before.note
        )
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        provider.disconnect()


if __name__ == "__main__":
    sys.exit(main())
