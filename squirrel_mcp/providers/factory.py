"""Provider selection.

Maps ``config.mail_provider`` to a concrete ``MailProvider`` through
``MAIL_PROVIDER_REGISTRY`` -- one entry per backend, loaded lazily so only the
selected backend's dependencies are imported. Adding Gmail/Outlook later is a
new entry here plus a new subpackage -- nothing in the tool layer moves.

The registry mirrors ``pan_mail_pro``'s ``PROVIDER_CLIENTS``: the set of names
lives in exactly one place, and ``tests/test_provider_contract.py`` asserts it
agrees with ``config.SUPPORTED_MAIL_PROVIDERS`` and that every registered class
implements the whole ``MailProvider`` protocol.
"""

from __future__ import annotations

from typing import Callable, Dict, Type

from ..config import SUPPORTED_MAIL_PROVIDERS, SquirrelConfig
from .protocol import CalendarProvider, ContactsProvider, MailProvider


def _load_imap() -> Type:
    # Imported lazily so the IMAP backend only loads when selected.
    from .soverin import SoverinMailProvider

    return SoverinMailProvider


# One entry per backend Squirrel can build. "imap" is any IMAP/SMTP mailbox --
# you supply the host names yourself; there are no per-provider presets. A
# future API-based backend ("gmail", "outlook") registers its loader here.
MAIL_PROVIDER_REGISTRY: Dict[str, Callable[[], Type]] = {
    "imap": _load_imap,
}


def create_mail_provider(config: SquirrelConfig) -> MailProvider:
    """Instantiate the mail provider named by ``config.mail_provider``.

    The provider is returned unconnected; the server calls ``connect()`` during
    startup so credential/connection errors surface early.
    """
    loader = MAIL_PROVIDER_REGISTRY.get(config.mail_provider)
    if loader is None:
        raise ValueError(
            f"Unsupported mail provider: {config.mail_provider!r}. "
            f"Supported: {', '.join(sorted(MAIL_PROVIDER_REGISTRY))}."
        )
    return loader()(config)


def create_calendar_provider(config: SquirrelConfig) -> CalendarProvider:
    """Instantiate the calendar (CalDAV) provider. Same account creds as mail."""
    from .soverin import SoverinCalendarProvider

    return SoverinCalendarProvider(config)


def create_contacts_provider(config: SquirrelConfig) -> ContactsProvider:
    """Instantiate the contacts (CardDAV) provider. Same account creds as mail."""
    from .soverin import SoverinContactsProvider

    return SoverinContactsProvider(config)


__all__ = [
    "MAIL_PROVIDER_REGISTRY",
    "SUPPORTED_MAIL_PROVIDERS",
    "create_mail_provider",
    "create_calendar_provider",
    "create_contacts_provider",
]
