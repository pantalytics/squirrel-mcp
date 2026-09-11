"""Pydantic models for structured tool output.

These are the client-facing shapes. The tool layer maps the provider's
transport-neutral dataclasses (``providers.protocol``) onto these.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class MailAccount(BaseModel):
    id: str = Field(description="Stable identifier; pass as the 'account' arg")
    email: str = Field(description="The address mail from this account is sent from")
    default: bool = Field(
        default=False,
        description="True when this account is used if 'account' is omitted",
    )


class MailAccountList(BaseModel):
    accounts: List[MailAccount] = Field(description="Email accounts this server can resolve")


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
    matched: str = Field(
        default="all",
        description=(
            "How the query was matched. 'all' = every term was required and "
            "found. 'partial' = nothing matched all of them, so the least "
            "distinctive terms were dropped and these results match the rest -- "
            "see dropped_terms and say so when reporting them. 'none' = nothing "
            "matched even then."
        ),
    )
    searched_terms: List[str] = Field(
        default_factory=list,
        description="The terms the query was understood as, after parsing",
    )
    dropped_terms: List[str] = Field(
        default_factory=list,
        description=(
            "Terms given up to get any results at all. Non-empty means these "
            "messages do NOT contain them -- tell the user which words were "
            "ignored rather than presenting the hits as exact."
        ),
    )


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
    reply_to_addrs: List[str] = Field(
        default_factory=list,
        description="Reply-To, when the sender set one -- where a reply belongs",
    )
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
    from_address: Optional[str] = Field(
        default=None, description="Address the draft will be sent from"
    )
    subject: Optional[str] = Field(
        default=None, description="Subject actually used (derived when replying)"
    )
    recipients: List[str] = Field(
        default_factory=list, description="Addresses in To (derived when replying)"
    )
    in_reply_to: Optional[str] = Field(
        default=None,
        description="Message-ID this draft replies to; null for a new conversation",
    )
    attachments: Optional[List[str]] = Field(
        default=None,
        description="Filenames attached to the draft. Null after an edit that left "
        "the draft's existing attachments alone -- which is not the same as none.",
    )


class SendResult(BaseModel):
    status: str = Field(description="Human-readable outcome")
    message_id: Optional[str] = None
    recipients: List[str] = Field(default_factory=list)
    from_address: Optional[str] = Field(
        default=None, description="Address the message was sent from"
    )
    subject: Optional[str] = Field(
        default=None, description="Subject actually used (derived when replying)"
    )
    in_reply_to: Optional[str] = Field(
        default=None,
        description="Message-ID this reply threads onto; null if sent as a new "
        "conversation. Repeat it back so the user knows it landed in the thread.",
    )
    attachments: List[str] = Field(
        default_factory=list,
        description="Filenames that went out with the message -- repeat them back",
    )
    saved_to_sent: Optional[bool] = Field(
        default=None,
        description=(
            "Whether a copy of the message was filed in the account's Sent "
            "folder. False means the message DID go out but the copy failed, "
            "so it will not be in Sent -- say so; the send itself still "
            "succeeded and must not be repeated. Null from a backend that "
            "does not report it."
        ),
    )
    sent_folder: Optional[str] = Field(
        default=None,
        description="Folder the copy was filed in (pass it to mail_search to find it)",
    )


class MoveResult(BaseModel):
    moved: int = Field(description="Number of messages moved")
    source_folder: str
    destination_folder: str
    uids: List[str] = Field(default_factory=list)


class FlagResult(BaseModel):
    changed: int = Field(
        description="Number of messages the flag change was applied to "
        "(a uid no longer in the folder is skipped)"
    )
    flagged: bool = Field(description="True if the flag was set, false if it was cleared")
    folder: str
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
    join_url: Optional[str] = Field(
        default=None, description="Link to join the online meeting, if the event has one"
    )


class EventWriteResult(BaseModel):
    uid: str
    calendar: str
    status: str
    join_url: Optional[str] = Field(
        default=None, description="Link to join the online meeting, when one was created"
    )


# ===================== Contacts (CardDAV) =====================


class AddressBookOut(BaseModel):
    id: str = Field(description="Address book id; pass as the 'addressbook' argument")
    name: str
    count: Optional[int] = None


class AddressBookList(BaseModel):
    addressbooks: List[AddressBookOut]


class ContactAddressOut(BaseModel):
    """One postal address: vCard ADR, component by component.

    Also the shape ``contacts_create`` / ``contacts_update`` take in
    ``addresses`` -- every field optional, at least one filled in.
    """

    type: Literal["home", "work"] = Field(default="home", description="home or work")
    street: str = Field(default="", description="Street and number, e.g. 'Jura 28'")
    extended: str = Field(default="", description="Flat, floor, building; usually empty")
    po_box: str = Field(default="", description="PO box; usually empty")
    city: str = Field(default="", description="e.g. 'Almelo'")
    region: str = Field(default="", description="Province or state; usually empty")
    postal_code: str = Field(default="", description="e.g. '7607 RG'")
    country: str = Field(default="", description="e.g. 'Netherlands'")
    preferred: bool = Field(default=False, description="The contact's main address")


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
    addresses: List[ContactAddressOut] = Field(
        default_factory=list, description="Postal addresses, in vCard order; [] when none"
    )


class ContactWriteResult(BaseModel):
    uid: str
    addressbook: str
    status: str
