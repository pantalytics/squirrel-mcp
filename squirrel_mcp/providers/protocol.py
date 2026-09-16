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

from ..search_query import MailQuery


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


# The SPECIAL-USE attributes (RFC 6154) a server puts on the folders it means
# as Sent, Trash, Archive, Junk and Drafts. Asking for them is the whole reason
# not to hardcode a name: the sent folder is "Sent Messages" on one host,
# "Verzonden items" on a Dutch Exchange and "INBOX.Sent" wherever INBOX is the
# namespace root, and every one of those answers to one flag. The same is true
# of the other four -- "Prullenbak", "Archief", "Spam" -- which is why this
# started as the Sent lookup and became a table rather than being copied four
# times.
#
# The value is the fallback used when a server advertises nothing: the name
# IMAP clients have defaulted to for decades. A wrong fallback fails loudly on
# the next command rather than quietly filing mail where nobody looks.
SPECIAL_USE_FOLDERS = {
    "sent": ("\\sent", "Sent"),
    "trash": ("\\trash", "Trash"),
    "archive": ("\\archive", "Archive"),
    "junk": ("\\junk", "Junk"),
    "drafts": ("\\drafts", "Drafts"),
}


def folder_role(flags) -> Optional[str]:
    """Which of the five special roles a folder's LIST flags claim, if any.

    The reverse of the table above, and the thing the tool layer reports: a
    caller that has been told "Archief is the archive" never has to guess a
    localized name, which is what ``mail_move(destination="Archive")`` was
    doing every time it was asked to file something away.
    """
    lowered = {str(f).lower() for f in flags or []}
    for role, (attribute, _fallback) in SPECIAL_USE_FOLDERS.items():
        if attribute in lowered:
            return role
    return None


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
    # Where the sender asked replies to go, when they said so at all. Optional
    # with a default so a backend written before replying existed still
    # constructs; a reply falls back to ``from_addr`` when it is empty.
    reply_to_addrs: List[str] = field(default_factory=list)


@dataclass
class AttachmentPayload:
    filename: str
    content_type: str
    size: int
    content: bytes


@dataclass
class OutgoingAttachment:
    """A file to hang on a message we are about to send or draft.

    Deliberately just bytes plus a name and a type: *how* that becomes an
    attachment is the transport's business, exactly like threading. Over
    IMAP/SMTP it turns into a MIME part in a ``multipart/mixed``; an API
    backend posts it to whatever attachment collection it keeps.

    ``inline`` and ``content_id`` are the HTML compose path this dataclass once
    said it was waiting for. The reasoning that kept them out still holds and
    is now enforced rather than avoided: a ``multipart/related`` holding an
    image nothing points at renders differently in every client, so an inline
    part is only *placed* inline when there is a ``body_html`` that could carry
    the ``cid:`` reference, and it degrades to an honest attachment when there
    is not. The tool layer fills a ``content_id`` in for every inline part,
    since a part nothing can name is a part nothing can show.
    """

    filename: str
    content_type: str
    content: bytes
    inline: bool = False
    content_id: Optional[str] = None

    @property
    def size(self) -> int:
        return len(self.content)


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
    def supports_outgoing_attachments(self) -> bool:
        """Whether ``send``/``save_draft`` here can carry attachments.

        Read through ``getattr(provider, ..., False)``, so a backend written
        before attachments existed answers False without being touched. That
        default is the point: a provider that ignores an ``attachments`` kwarg
        would send the mail *without* the file and report success, and silent
        data loss is the one outcome worse than a refusal. The tool layer
        checks this before it accepts a single byte.
        """
        ...

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
        flagged_only: bool = False,
        since: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
        parsed: Optional["MailQuery"] = None,
    ) -> Tuple[List[MessageSummary], int]:
        """Search a folder. Returns (page of summaries, total matching count).

        ``query`` is free text matched against the message (subject/body/headers).
        ``flagged_only`` narrows to messages carrying ``\\Flagged`` (see
        ``flag``). ``since`` is an ISO date (YYYY-MM-DD) lower bound. Newest
        first.

        ``parsed`` is that same ``query`` already run through
        ``search_query.parse`` -- the terms, the field scopes, the negations and
        the quoted phrases, in the one grammar every backend compiles. Honour it
        when it is given: it is what stops each backend inventing its own
        meaning for a multi-word query, which is what ``TEXT "Iris van 't
        Klooster"`` did on IMAP and ``$search`` did on Graph.

        Two rules go with it, and the tool layer depends on both:

        * **Compile it at least as broadly as it reads.** A term that cannot be
          sent faithfully -- an accent an IMAP server will not fold, a phrase a
          keyword index cannot keep together -- is widened, never narrowed. The
          tool layer re-checks every field a summary can prove and takes the
          breadth back; a match dropped down here is gone for good.
        * **It is an addition, not a replacement.** ``query`` stays
          authoritative, so a backend written before this argument existed (or
          one that would rather use its own engine) keeps working by ignoring
          it, and one that wants the structure without being handed it can call
          ``parse(query)`` itself.
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
        reply_to_uid: Optional[str] = None,
        reply_to_folder: str = "INBOX",
        attachments: Optional[List[OutgoingAttachment]] = None,
        body_html: Optional[str] = None,
    ) -> str:
        """Append a new draft to the Drafts folder. Returns its uid.

        ``reply_to_uid`` threads the draft onto that message -- see ``send``.
        ``attachments`` requires ``supports_outgoing_attachments``.
        ``body_html`` is what ``send`` documents.
        """
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
        attachments: Optional[List[OutgoingAttachment]] = None,
        body_html: Optional[str] = None,
    ) -> str:
        """Replace an existing draft. Returns the new uid.

        A draft that was created as a reply stays one: the implementation
        carries its threading over. There is deliberately no ``reply_to_uid``
        here -- a draft can only be threaded when it is created (Graph mints
        the conversation then), so turning an ordinary draft into a reply means
        saving a new one.

        ``attachments`` follows the same rule as the threading, for the same
        reason: ``None`` means *keep what the draft already carries*, because
        an edit rewrites the message and a file the user attached a minute ago
        would otherwise vanish at the moment they fixed a typo. Pass ``[]`` to
        deliberately strip them.
        """
        ...

    def send(
        self,
        to: List[str],
        subject: str,
        body: str,
        *,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        reply_to_uid: Optional[str] = None,
        reply_to_folder: str = "INBOX",
        attachments: Optional[List[OutgoingAttachment]] = None,
        body_html: Optional[str] = None,
    ) -> dict:
        """Send a message. Returns {'message_id', 'recipients', 'saved_to_sent', 'sent_folder'}.

        **Sending is only half of it: the copy in Sent is the other half.** A
        transport delivers a message to its recipients and nothing more, so
        unless the backend's server files a copy itself, the sender's own
        record of it does not exist -- the mail went out and ``mail_search``
        on Sent finds nothing, which is what a user reads as "it wasn't sent".
        Filing that copy is therefore part of this method's contract, and each
        backend does it the way its transport does: IMAP/SMTP APPENDs the sent
        bytes to the ``\\Sent`` folder itself, while Graph's ``/send`` puts the
        message in Sent Items server-side and the backend simply reports it.

        ``saved_to_sent`` says whether the copy is there (None from a backend
        old enough not to answer), and ``sent_folder`` names where. **False is
        not a failed send** -- the message left -- so an implementation reports
        it rather than raising: raising would invite a retry that delivers the
        message twice.

        ``reply_to_uid`` (a uid in ``reply_to_folder``) makes this a reply
        *inside* that message's thread. How is the backend's business: an
        IMAP/SMTP backend reads the parent's Message-ID and References and
        sends the matching headers; an API backend may have a conversation of
        its own to join. Recipients and subject are not derived here -- the
        caller passes what it means to send.

        ``body_html`` is the rich half of the same message: ``body`` stays the
        text a client without HTML shows, and a backend that can carry both
        carries both. It is also the only thing that makes an ``inline``
        attachment visible, since what displays a part is the body's ``cid:``
        reference to it -- so a backend with nowhere to put a second body
        (Graph's message has one) says so rather than pretending.
        """
        ...

    def send_draft(self, folder: str, uid: str) -> dict:
        """Send a draft that already exists, and take it out of ``folder``.

        Returns what ``send`` returns plus ``'subject'``, ``'attachments'``
        (filenames) and ``'draft_removed'``.

        **What goes out is the draft, not a copy of its fields.** The tool
        layer deliberately hands over a uid and nothing else: a draft is
        already a complete message, and anything rebuilt from the parts the
        tools model would lose the rest -- the MIME tree an embedded image
        needs, the threading that makes it a reply, a header another client
        wrote. The user approved what they read; that is what must leave.

        The same never-fatal rule as the Sent copy applies to removing the
        draft, and for the same reason: by then the message is with the
        recipient, so a failure here is reported in ``draft_removed`` rather
        than raised. Raising would say a delivered message was not delivered
        and invite a retry that sends it twice.

        The tool layer reads this through ``getattr(provider, "send_draft",
        None)`` and refuses with an alternative when a backend has not got it,
        so an older backend is told about rather than crashed into.
        """
        ...

    def move(self, folder: str, uids: List[str], destination: str) -> int:
        """Move messages from ``folder`` to ``destination``. Returns count moved."""
        ...

    def delete(self, folder: str, uids: List[str]) -> Tuple[int, str]:
        """Throw messages away. Returns (count deleted, the folder they went to).

        **This is a mail client's delete key, not an erase.** Every backend
        files the messages in whatever it calls Trash, where the user can still
        get them back -- which is the "could the user not get this back" line
        the rest of this package draws. An implementation that permanently
        removed them would be answering a different question than the one the
        tool asked, and the tool has no way to tell.

        The destination is the backend's to find, not the caller's to name --
        it is localized ("Prullenbak"), sometimes under INBOX, and IMAP
        advertises it as the ``\\Trash`` SPECIAL-USE attribute (RFC 6154)
        exactly as it advertises ``\\Sent``. Returning the name is how the
        tool tells the user where to look.

        Read through ``getattr(provider, "delete", None)``, so a backend
        written before this refuses clearly rather than crashing.
        """
        ...

    def flag(self, folder: str, uids: List[str], flagged: bool = True) -> int:
        """Set or clear the ``\\Flagged`` marker on messages. Returns count changed.

        The marker every mail client draws as a star/flag. Setting it on an
        already-flagged message is a no-op, not an error -- the operation is
        idempotent, and so is clearing it.
        """
        ...

    def set_seen(self, folder: str, uids: List[str], seen: bool = True) -> int:
        """Set or clear the ``\\Seen`` marker on messages. Returns count changed.

        The read/unread state every mail client draws as bold-or-not. A
        sibling of ``flag`` rather than an argument to it: they are two
        independent markers, and a tool that took both would have to be told
        which one it was not changing.

        Reading a message does *not* set it -- ``read`` fetches without
        marking, so what is unread stays the user's answer to "what have I not
        looked at", not a side effect of an agent looking.
        """
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
    # Where an attendee joins the online meeting, when the event has one. Only a
    # backend that can mint such a link ever fills this; CalDAV leaves it None,
    # which is why it carries a default -- an older backend needs no change.
    join_url: Optional[str] = None


@runtime_checkable
class CalendarProvider(Protocol):
    """Interface every calendar backend must satisfy (CalDAV today)."""

    @property
    def is_authenticated(self) -> bool: ...

    @property
    def supports_attendees(self) -> bool:
        """Whether naming attendees here actually *invites* them.

        Read through ``getattr(provider, ..., False)``, so a backend written
        before invitations existed answers False without being touched -- the
        same default the mail pillar's ``supports_outgoing_attachments`` takes,
        and for the same reason. Listing an ``ATTENDEE`` in an iCalendar object
        is not the same as delivering an invitation: that needs server-side
        scheduling (RFC 6638), which some CalDAV servers implement and others
        silently do not, and nothing in the protocol lets a client tell which
        it is talking to. A caller who believes they invited five people and
        actually invited none is a worse outcome than a refusal, so a backend
        claims this only when the invitation is the server's job and it knows
        the server does it.
        """
        ...

    @property
    def supports_online_meeting(self) -> bool:
        """Whether this backend can attach an online meeting to a new event.

        True on a backend whose API mints the conference itself -- Microsoft
        Graph returns a Teams meeting for ``isOnlineMeeting``. There is no
        CalDAV equivalent: a plain calendar server stores whatever conferencing
        details it is handed and creates none, so it answers False rather than
        writing an event that looks like a meeting nobody can join.
        """
        ...

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
        attendees: Optional[List[str]] = None,
        online_meeting: bool = False,
    ) -> str:
        """Create an event. Returns its uid.

        ``attendees`` (addresses) turns the event into a meeting and invites
        them; ``online_meeting`` asks the backend to mint a conference link for
        it. Both are gated by the capability properties above -- the tool layer
        refuses before it gets here -- so an implementation may accept them and
        raise if it declared the matching capability False.
        """
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
class ContactAddress:
    """One postal address -- vCard's seven ``ADR`` components plus its params.

    The components are named, not positional, so a backend that keeps them in
    a different shape (Graph's ``homeAddress`` / ``businessAddress``) maps the
    ones it has and leaves the rest empty. ``type`` is ``home`` or ``work``;
    ``preferred`` is vCard's ``PREF``.
    """

    type: str = "home"
    street: str = ""
    extended: str = ""
    po_box: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""
    preferred: bool = False


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
    # Default so a backend written before addresses existed still constructs;
    # the tool reports an empty list, never null.
    addresses: List[ContactAddress] = field(default_factory=list)


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
        addresses: Optional[List[ContactAddress]] = None,
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
        addresses: Optional[List[ContactAddress]] = None,
    ) -> str:
        """Update a contact in place. ``None`` leaves a field untouched.

        ``emails`` / ``phones`` / ``addresses`` replace the whole list when
        given (``[]`` clears it). Everything the backend does not model --
        ``PHOTO``, ``CATEGORIES``, ``X-*`` -- must survive the write: this is
        read-modify-write on the full record, never a rebuild from the fields
        the tool knows.
        """
        ...

    def delete_contact(self, addressbook: str, uid: str) -> None: ...
