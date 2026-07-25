"""Provider layer for Squirrel.

Tools talk only to the protocols defined here; concrete backends (Soverin now,
Gmail/Outlook later) live in subpackages and are selected by ``factory``.
"""

from .factory import (
    create_calendar_provider,
    create_contacts_provider,
    create_mail_provider,
)
from .protocol import (
    AddressBookInfo,
    AttachmentInfo,
    AttachmentPayload,
    CalendarInfo,
    CalendarProvider,
    ContactDetail,
    ContactsProvider,
    ContactSummary,
    EventDetail,
    EventSummary,
    FolderInfo,
    MailAuthError,
    MailNotFoundError,
    MailProvider,
    MailProviderError,
    MessageDetail,
    MessageSummary,
    ProviderAuthError,
    ProviderError,
    ProviderNotFoundError,
)

__all__ = [
    "create_mail_provider",
    "create_calendar_provider",
    "create_contacts_provider",
    "MailProvider",
    "CalendarProvider",
    "ContactsProvider",
    "ProviderError",
    "ProviderAuthError",
    "ProviderNotFoundError",
    "MailProviderError",
    "MailAuthError",
    "MailNotFoundError",
    "FolderInfo",
    "MessageSummary",
    "MessageDetail",
    "AttachmentInfo",
    "AttachmentPayload",
    "CalendarInfo",
    "EventSummary",
    "EventDetail",
    "AddressBookInfo",
    "ContactSummary",
    "ContactDetail",
]
