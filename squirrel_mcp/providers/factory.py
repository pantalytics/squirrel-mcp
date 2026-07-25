"""Provider selection.

Maps ``config.mail_provider`` to a concrete ``MailProvider``. Adding Gmail/Outlook
later is a new branch here plus a new subpackage -- nothing in the tool layer moves.
"""

from __future__ import annotations

from ..config import SUPPORTED_MAIL_PROVIDERS, SquirrelConfig
from .protocol import CalendarProvider, ContactsProvider, MailProvider


def create_mail_provider(config: SquirrelConfig) -> MailProvider:
    """Instantiate the mail provider named by ``config.mail_provider``.

    Today the only backend is ``imap``: any IMAP/SMTP mailbox, with host/port/
    security taken straight from the config. A future API-based backend (Gmail,
    Outlook) would branch here to its own ``MailProvider`` implementation.

    The provider is returned unconnected; the server calls ``connect()`` during
    startup so credential/connection errors surface early.
    """
    provider = config.mail_provider

    if provider in SUPPORTED_MAIL_PROVIDERS:
        # Imported lazily so the IMAP backend only loads when selected.
        from .soverin import SoverinMailProvider

        return SoverinMailProvider(config)

    raise ValueError(
        f"Unsupported mail provider: {provider!r}. "
        f"Supported: {', '.join(sorted(SUPPORTED_MAIL_PROVIDERS))}."
    )


def create_calendar_provider(config: SquirrelConfig) -> CalendarProvider:
    """Instantiate the calendar (CalDAV) provider. Same account creds as mail."""
    from .soverin import SoverinCalendarProvider

    return SoverinCalendarProvider(config)


def create_contacts_provider(config: SquirrelConfig) -> ContactsProvider:
    """Instantiate the contacts (CardDAV) provider. Same account creds as mail."""
    from .soverin import SoverinContactsProvider

    return SoverinContactsProvider(config)
