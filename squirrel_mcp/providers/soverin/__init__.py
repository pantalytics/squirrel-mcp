"""Soverin backends (IMAP/SMTP mail, CalDAV calendar, CardDAV contacts)."""

from .calendar import SoverinCalendarProvider
from .contacts import SoverinContactsProvider
from .provider import SoverinMailProvider

__all__ = [
    "SoverinMailProvider",
    "SoverinCalendarProvider",
    "SoverinContactsProvider",
]
