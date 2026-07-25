"""Pydantic models for structured tool output.

These are the client-facing shapes. The tool layer maps the provider's
transport-neutral dataclasses (``providers.protocol``) onto these.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class FolderInfo(BaseModel):
    name: str = Field(description="Folder / mailbox name (use this as the 'folder' arg)")
    delimiter: str = Field(description="Hierarchy delimiter used by the server")
    flags: List[str] = Field(default_factory=list, description="IMAP folder flags")


class FolderList(BaseModel):
    folders: List[FolderInfo] = Field(description="All mailboxes/folders")


class MessageSummary(BaseModel):
    uid: str = Field(description="Per-folder message id; pass to mail_read in this folder")
    folder: str = Field(description="Folder the message lives in")
    subject: str
    from_addr: str = Field(description="Sender (name + address when available)")
    to_addrs: List[str] = Field(default_factory=list)
    date: Optional[str] = Field(default=None, description="Message Date header")
    flags: List[str] = Field(default_factory=list, description="e.g. \\Seen, \\Flagged")
    size: int = Field(description="Approximate message size in bytes")
    has_attachments: bool
    preview: str = Field(description="First ~200 chars of the text body")


class SearchResult(BaseModel):
    messages: List[MessageSummary] = Field(description="Page of matching messages, newest first")
    total: int = Field(description="Total messages matching the query in this folder")
    limit: int = Field(description="Max messages returned per page")
    offset: int = Field(description="Messages skipped")
    folder: str


class AttachmentMeta(BaseModel):
    index: int = Field(description="Pass to mail_get_attachment to download it")
    filename: str
    content_type: str
    size: int = Field(description="Attachment size in bytes")


class MailBody(BaseModel):
    uid: str
    folder: str
    subject: str
    from_addr: str
    to_addrs: List[str] = Field(default_factory=list)
    cc_addrs: List[str] = Field(default_factory=list)
    date: Optional[str] = None
    flags: List[str] = Field(default_factory=list)
    message_id: Optional[str] = None
    body: str = Field(description="Text body, possibly truncated (see is_truncated)")
    body_length: int = Field(description="Full body length in characters")
    is_truncated: bool = Field(description="If true, page the rest with mail_read_chunk")
    attachments: List[AttachmentMeta] = Field(default_factory=list)


class MailChunk(BaseModel):
    uid: str
    folder: str
    chunk: str = Field(description="Slice of the body")
    offset: int
    length: int = Field(description="Characters returned in this chunk")
    body_length: int = Field(description="Full body length in characters")
    has_more: bool = Field(description="If true, request the next offset")


class AttachmentContent(BaseModel):
    filename: str
    content_type: str
    size: int
    data_base64: str = Field(description="Attachment bytes, base64-encoded")


class DraftResult(BaseModel):
    uid: str = Field(description="Uid of the saved draft (empty if the server hid it)")
    folder: str
    status: str = Field(description="Human-readable outcome")


class SendResult(BaseModel):
    status: str = Field(description="Human-readable outcome")
    message_id: Optional[str] = None
    recipients: List[str] = Field(default_factory=list)


class MoveResult(BaseModel):
    moved: int = Field(description="Number of messages moved")
    source_folder: str
    destination_folder: str
    uids: List[str] = Field(default_factory=list)


# ===================== Calendar (CalDAV) =====================


class CalendarOut(BaseModel):
    id: str = Field(description="Calendar id; pass as the 'calendar' argument")
    name: str
    description: Optional[str] = None


class CalendarList(BaseModel):
    calendars: List[CalendarOut]


class EventOut(BaseModel):
    uid: str = Field(description="Event id; pass to calendar_read_event")
    calendar: str
    summary: str
    start: Optional[str] = Field(default=None, description="ISO 8601 start")
    end: Optional[str] = Field(default=None, description="ISO 8601 end")
    all_day: bool = False
    location: Optional[str] = None


class EventList(BaseModel):
    events: List[EventOut] = Field(description="Matching events, newest first")
    count: int
    calendar: str


class EventDetailOut(EventOut):
    description: Optional[str] = None
    organizer: Optional[str] = None
    attendees: List[str] = Field(default_factory=list)
    status: Optional[str] = None


class EventWriteResult(BaseModel):
    uid: str
    calendar: str
    status: str


# ===================== Contacts (CardDAV) =====================


class AddressBookOut(BaseModel):
    id: str = Field(description="Address book id; pass as the 'addressbook' argument")
    name: str
    count: Optional[int] = None


class AddressBookList(BaseModel):
    addressbooks: List[AddressBookOut]


class ContactOut(BaseModel):
    uid: str = Field(description="Contact id; pass to contacts_read")
    addressbook: str
    full_name: str
    emails: List[str] = Field(default_factory=list)
    phones: List[str] = Field(default_factory=list)
    organization: Optional[str] = None


class ContactList(BaseModel):
    contacts: List[ContactOut] = Field(description="Page of contacts, A→Z")
    total: int
    limit: int
    offset: int
    addressbook: str


class ContactDetailOut(ContactOut):
    title: Optional[str] = None
    note: Optional[str] = None


class ContactWriteResult(BaseModel):
    uid: str
    addressbook: str
    status: str
