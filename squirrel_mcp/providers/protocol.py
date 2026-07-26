"""Provider-agnostic mail interface.

``MailProvider`` is the seam that makes backends swappable: the tool layer only
ever touches this protocol and the plain dataclasses below, never a concrete
IMAP/SMTP/API client. Soverin satisfies it today; Gmail/Outlook can satisfy it
later without any change to the tools.

The dataclasses are deliberately transport-neutral (no pydantic, no MCP types) so
a provider implementation depends on nothing above it. The tool layer maps them
to the pydantic response models in ``schemas.py``.

Reserved for the other two pillars (not built in v1): ``ContactsProvider`` and
``CalendarProvider`` will live alongside this one and follow the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple, runtime_checkable


# --------------------------------------------------------------------------- #
# Errors raised by provider implementations. The tool layer catches these and
# re-raises them as sanitized MCP errors.
# --------------------------------------------------------------------------- #
class ProviderError(Exception):
    """Base error for any provider (mail / contacts / calendar) failure."""


class ProviderAuthError(ProviderError):
    """Login / credential failure."""


class ProviderNotFoundError(ProviderError):
    """A requested resource does not exist."""


# Mail-specific aliases (kept for backwards compatibility / readability).
class MailProviderError(ProviderError):
    """Base error for any mail-provider failure."""


class MailAuthError(ProviderAuthError, MailProviderError):
    """Login / credential failure."""


class MailNotFoundError(ProviderNotFoundError, MailProviderError):
    """A requested folder, message or attachment does not exist."""


# --------------------------------------------------------------------------- #
# Transport-neutral value objects.
# --------------------------------------------------------------------------- #
@dataclass
class FolderInfo:
    name: str
    delimiter: str = "/"
    flags: List[str] = field(default_factory=list)


@dataclass
class MessageSummary:
    uid: str
    folder: str
    subject: str
    from_addr: str
    to_addrs: List[str]
    date: Optional[str]
    flags: List[str]
    size: int
    has_attachments: bool
    preview: str


@dataclass
class AttachmentInfo:
    index: int
    filename: str
    content_type: str
    size: int


@dataclass
class MessageDetail:
    uid: str
    folder: str
    subject: str
    from_addr: str
    to_addrs: List[str]
    cc_addrs: List[str]
    date: Optional[str]
    flags: List[str]
    message_id: Optional[str]
    body_text: str
    body_length: int
    attachments: List[AttachmentInfo]


@dataclass
class AttachmentPayload:
    filename: str
    content_type: str
    size: int
    content: bytes


@runtime_checkable
class MailProvider(Protocol):
    """Interface every mail backend must satisfy.

    Implementations are synchronous (IMAP/SMTP are blocking). The tool layer runs
    them off the event loop via ``run_blocking`` and serializes calls to the same
    provider with a lock, so implementations need not be thread-safe themselves.
    """

    @property
    def is_authenticated(self) -> bool: ...

    @property
    def email(self) -> str:
        """The address mail from this provider is sent from (the From header)."""
        ...

    def connect(self) -> None:
        """Open the connection and log in. Raises MailAuthError on bad creds."""
        ...

    def disconnect(self) -> None:
        """Close the connection. Must be safe to call when not connected."""
        ...

    def authenticate(self) -> None:
        """Ensure the session is logged in, reconnecting if it dropped."""
        ...

    def list_folders(self) -> List[FolderInfo]:
        """List all mailboxes/folders."""
        ...

    def search(
        self,
        folder: str,
        query: Optional[str] = None,
        *,
        unseen_only: bool = False,
        since: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[MessageSummary], int]:
        """Search a folder. Returns (page of summaries, total matching count).

        ``query`` is free text matched against the message (subject/body/headers).
        ``since`` is an ISO date (YYYY-MM-DD) lower bound. Newest first.
        """
        ...

    def fetch_message(self, folder: str, uid: str) -> MessageDetail:
        """Fetch one message's headers + text body + attachment metadata."""
        ...

    def fetch_attachment(self, folder: str, uid: str, index: int) -> AttachmentPayload:
        """Fetch one attachment's bytes by its index in the message."""
        ...

    def save_draft(
        self,
        to: List[str],
        subject: str,
        body: str,
        *,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        folder: str = "Drafts",
    ) -> str:
        """Append a new draft to the Drafts folder. Returns its uid."""
        ...

    def update_draft(
        self,
        folder: str,
        uid: str,
        to: List[str],
        subject: str,
        body: str,
        *,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> str:
        """Replace an existing draft. Returns the new uid."""
        ...

    def send(
        self,
        to: List[str],
        subject: str,
        body: str,
        *,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> dict:
        """Send a message. Returns {'message_id': ..., 'recipients': [...]}."""
        ...

    def move(self, folder: str, uids: List[str], destination: str) -> int:
        """Move messages from ``folder`` to ``destination``. Returns count moved."""
        ...


# --------------------------------------------------------------------------- #
# Calendar pillar (CalDAV) -- value objects + protocol.
# --------------------------------------------------------------------------- #
@dataclass
class CalendarInfo:
    id: str  # a stable identifier (the calendar URL/path)
    name: str
    description: Optional[str] = None


@dataclass
class EventSummary:
    uid: str
    calendar: str
    summary: str
    start: Optional[str]  # ISO 8601
    end: Optional[str]
    all_day: bool
    location: Optional[str]


@dataclass
class EventDetail:
    uid: str
    calendar: str
    summary: str
    start: Optional[str]
    end: Optional[str]
    all_day: bool
    location: Optional[str]
    description: Optional[str]
    organizer: Optional[str]
    attendees: List[str]
    status: Optional[str]


@runtime_checkable
class CalendarProvider(Protocol):
    """Interface every calendar backend must satisfy (CalDAV today)."""

    @property
    def is_authenticated(self) -> bool: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def authenticate(self) -> None: ...

    def list_calendars(self) -> List[CalendarInfo]: ...

    def search_events(
        self,
        calendar: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 50,
    ) -> List[EventSummary]:
        """Events in a calendar within [start, end] (ISO dates), newest first."""
        ...

    def get_event(self, calendar: str, uid: str) -> EventDetail: ...

    def create_event(
        self,
        calendar: str,
        summary: str,
        start: str,
        end: str,
        *,
        all_day: bool = False,
        location: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        """Create an event. Returns its uid."""
        ...

    def update_event(
        self,
        calendar: str,
        uid: str,
        *,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str: ...

    def delete_event(self, calendar: str, uid: str) -> None: ...


# --------------------------------------------------------------------------- #
# Contacts pillar (CardDAV) -- value objects + protocol.
# --------------------------------------------------------------------------- #
@dataclass
class AddressBookInfo:
    id: str  # the address book URL/path
    name: str
    count: Optional[int] = None


@dataclass
class ContactSummary:
    uid: str
    addressbook: str
    full_name: str
    emails: List[str]
    phones: List[str]
    organization: Optional[str]


@dataclass
class ContactDetail:
    uid: str
    addressbook: str
    full_name: str
    emails: List[str]
    phones: List[str]
    organization: Optional[str]
    title: Optional[str]
    note: Optional[str]


@runtime_checkable
class ContactsProvider(Protocol):
    """Interface every contacts backend must satisfy (CardDAV today)."""

    @property
    def is_authenticated(self) -> bool: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def authenticate(self) -> None: ...

    def list_addressbooks(self) -> List[AddressBookInfo]: ...

    def search_contacts(
        self,
        addressbook: str,
        *,
        query: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ContactSummary], int]:
        """Search an address book. Returns (page of summaries, total count)."""
        ...

    def get_contact(self, addressbook: str, uid: str) -> ContactDetail: ...

    def create_contact(
        self,
        addressbook: str,
        full_name: str,
        *,
        emails: Optional[List[str]] = None,
        phones: Optional[List[str]] = None,
        organization: Optional[str] = None,
    ) -> str:
        """Create a contact. Returns its uid."""
        ...

    def update_contact(
        self,
        addressbook: str,
        uid: str,
        *,
        full_name: Optional[str] = None,
        emails: Optional[List[str]] = None,
        phones: Optional[List[str]] = None,
        organization: Optional[str] = None,
    ) -> str: ...

    def delete_contact(self, addressbook: str, uid: str) -> None: ...
