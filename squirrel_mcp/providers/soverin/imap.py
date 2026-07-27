"""IMAP path for Soverin, built on the mature ``imap-tools`` library.

This is the only module that speaks IMAP. It returns the transport-neutral
dataclasses from ``providers.protocol`` so the provider and tool layers above it
stay backend-agnostic. Connections are kept open and transparently re-established
if the server drops them mid-session.
"""

from __future__ import annotations

import datetime
import imaplib
import re
import ssl
from typing import Callable, List, Optional, Tuple, TypeVar

from imap_tools import (
    AND,
    BaseMailBox,
    MailBox,
    MailBoxStartTls,
    MailBoxUnencrypted,
    MailMessageFlags,
)
from imap_tools.errors import MailboxFlagError, MailboxLoginError
from imap_tools.utils import check_command_status, clean_uids

from ...logging_config import get_logger
from ..protocol import (
    AttachmentInfo,
    AttachmentPayload,
    FolderInfo,
    MailAuthError,
    MailNotFoundError,
    MailProviderError,
    MessageDetail,
    MessageSummary,
)
from .mime import build_email

logger = get_logger(__name__)

T = TypeVar("T")

# Connection-level failures that warrant one transparent reconnect + retry.
_CONNECTION_ERRORS = (imaplib.IMAP4.abort, OSError, ConnectionError, EOFError)

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """Very small HTML -> text fallback for messages with no text/plain part."""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = _TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


class SoverinImapClient:
    """Persistent IMAP session with lazy login and reconnect-on-drop."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        email: str,
        drafts_folder: str = "Drafts",
        security: str = "ssl",
        tls_verify: bool = True,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._email = email
        self._drafts_folder = drafts_folder
        self._security = security
        self._tls_verify = tls_verify
        self._mailbox: Optional[BaseMailBox] = None

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self._tls_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    # ---- lifecycle ------------------------------------------------------- #
    @property
    def is_authenticated(self) -> bool:
        return self._mailbox is not None

    def connect(self) -> None:
        self._login()

    def authenticate(self) -> None:
        if self._mailbox is None:
            self._login()

    def disconnect(self) -> None:
        if self._mailbox is not None:
            try:
                self._mailbox.logout()
            except Exception:  # noqa: BLE001 - best-effort close
                pass
            self._mailbox = None

    def _login(self) -> None:
        try:
            if self._security == "plain":
                mailbox: BaseMailBox = MailBoxUnencrypted(self._host, port=self._port)
            elif self._security == "starttls":
                mailbox = MailBoxStartTls(
                    self._host, port=self._port, ssl_context=self._ssl_context()
                )
            else:  # "ssl" (implicit TLS) -- the Soverin default
                mailbox = MailBox(self._host, port=self._port, ssl_context=self._ssl_context())
            mailbox.login(self._username, self._password)
        except MailboxLoginError as exc:
            raise MailAuthError(f"IMAP login failed: {exc}") from exc
        except (OSError, imaplib.IMAP4.error) as exc:
            raise MailProviderError(f"Cannot connect to IMAP server: {exc}") from exc
        self._mailbox = mailbox
        logger.info("IMAP session established for %s", self._username)

    def _run(self, fn: Callable[[BaseMailBox], T]) -> T:
        """Run an operation against a live mailbox, reconnecting once on drop."""
        if self._mailbox is None:
            self._login()
        try:
            return fn(self._mailbox)  # type: ignore[arg-type]
        except _CONNECTION_ERRORS:
            logger.warning("IMAP connection dropped; reconnecting")
            self.disconnect()
            self._login()
            return fn(self._mailbox)  # type: ignore[arg-type]

    # ---- read operations ------------------------------------------------- #
    def list_folders(self) -> List[FolderInfo]:
        def op(mb: BaseMailBox) -> List[FolderInfo]:
            return [
                FolderInfo(name=f.name, delimiter=f.delim or "/", flags=list(f.flags))
                for f in mb.folder.list()
            ]

        return self._run(op)

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
    ) -> Tuple[List[MessageSummary], int]:
        criteria = self._build_criteria(query, unseen_only, flagged_only, since)
        charset = "UTF-8" if query else "US-ASCII"

        def op(mb: BaseMailBox) -> Tuple[List[MessageSummary], int]:
            self._select(mb, folder)
            all_uids = list(mb.uids(criteria, charset=charset))
            total = len(all_uids)
            # imap uids are ascending (oldest first) -> newest first for humans.
            all_uids.reverse()
            page = all_uids[offset : offset + limit]
            if not page:
                return [], total
            by_uid = {
                m.uid: m
                for m in mb.fetch(uid_list=page, mark_seen=False, bulk=True)
            }
            summaries = [self._to_summary(by_uid[u], folder) for u in page if u in by_uid]
            return summaries, total

        return self._run(op)

    def fetch_message(self, folder: str, uid: str) -> MessageDetail:
        def op(mb: BaseMailBox) -> MessageDetail:
            self._select(mb, folder)
            msgs = list(mb.fetch(uid_list=[uid], mark_seen=False, limit=1))
            if not msgs:
                raise MailNotFoundError(f"Message uid {uid} not found in {folder}")
            return self._to_detail(msgs[0], folder)

        return self._run(op)

    def fetch_attachment(self, folder: str, uid: str, index: int) -> AttachmentPayload:
        def op(mb: BaseMailBox) -> AttachmentPayload:
            self._select(mb, folder)
            msgs = list(mb.fetch(uid_list=[uid], mark_seen=False, limit=1))
            if not msgs:
                raise MailNotFoundError(f"Message uid {uid} not found in {folder}")
            attachments = msgs[0].attachments
            if index < 0 or index >= len(attachments):
                raise MailNotFoundError(
                    f"Attachment index {index} out of range "
                    f"(message has {len(attachments)} attachment(s))"
                )
            att = attachments[index]
            return AttachmentPayload(
                filename=att.filename or f"attachment-{index}",
                content_type=att.content_type or "application/octet-stream",
                size=len(att.payload),
                content=att.payload,
            )

        return self._run(op)

    # ---- write operations ------------------------------------------------ #
    def save_draft(
        self,
        to: List[str],
        subject: str,
        body: str,
        *,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        folder: Optional[str] = None,
    ) -> str:
        target = folder or self._drafts_folder
        msg = build_email(self._email, to, subject, body, cc=cc, bcc=bcc)
        message_id = msg["Message-ID"]

        def op(mb: BaseMailBox) -> str:
            mb.append(msg.as_bytes(), target, flag_set=[MailMessageFlags.DRAFT])
            self._select(mb, target)
            found = list(mb.uids(f'HEADER Message-ID "{message_id}"'))
            return found[-1] if found else ""

        return self._run(op)

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
        target = folder or self._drafts_folder

        def op(mb: BaseMailBox) -> str:
            self._select(mb, target)
            existing = list(mb.fetch(uid_list=[uid], mark_seen=False, limit=1))
            if not existing:
                raise MailNotFoundError(f"Draft uid {uid} not found in {target}")
            msg = build_email(self._email, to, subject, body, cc=cc, bcc=bcc)
            message_id = msg["Message-ID"]
            mb.append(msg.as_bytes(), target, flag_set=[MailMessageFlags.DRAFT])
            # Remove the old revision.
            mb.delete([uid])
            self._select(mb, target)
            found = list(mb.uids(f'HEADER Message-ID "{message_id}"'))
            return found[-1] if found else ""

        return self._run(op)

    def move(self, folder: str, uids: List[str], destination: str) -> int:
        def op(mb: BaseMailBox) -> int:
            self._select(mb, folder)
            mb.move(uids, destination)
            return len(uids)

        return self._run(op)

    def flag(self, folder: str, uids: List[str], flagged: bool = True) -> int:
        # Deliberately a raw UID STORE rather than imap_tools' ``mb.flag``:
        # that helper follows every STORE with an EXPUNGE, which permanently
        # removes anything another client left marked \Deleted in this folder.
        # Flagging is meant to be the one mail write you can undo, so it does
        # not get to delete messages as a side effect.
        try:
            # Rejects anything that is not a bare uid, so a crafted argument
            # cannot smuggle extra IMAP into the STORE command below.
            cleaned = clean_uids(uids)
        except TypeError as exc:
            raise MailProviderError(f"Invalid message uid: {exc}") from exc
        if not cleaned:
            return 0

        def op(mb: BaseMailBox) -> int:
            self._select(mb, folder)
            result = mb.client.uid(
                "STORE",
                ",".join(cleaned),
                ("+" if flagged else "-") + "FLAGS",
                f"({MailMessageFlags.FLAGGED})",
            )
            try:
                check_command_status(result, MailboxFlagError)
            except MailboxFlagError as exc:
                raise MailProviderError(f"Could not update flags: {exc}") from exc
            return len(cleaned)

        return self._run(op)

    # ---- helpers --------------------------------------------------------- #
    @staticmethod
    def _select(mb: MailBox, folder: str) -> None:
        try:
            mb.folder.set(folder)
        except Exception as exc:  # noqa: BLE001 - normalize to a domain error
            raise MailNotFoundError(f"Folder {folder!r} not found: {exc}") from exc

    @staticmethod
    def _build_criteria(
        query: Optional[str], unseen_only: bool, flagged_only: bool, since: Optional[str]
    ):
        kwargs: dict = {}
        if query:
            kwargs["text"] = query
        if unseen_only:
            kwargs["seen"] = False
        if flagged_only:
            # Only ever set when asked: ``flagged=False`` is not "no filter",
            # it is UNFLAGGED -- the exact opposite of what an omitted argument
            # should mean.
            kwargs["flagged"] = True
        if since:
            try:
                kwargs["date_gte"] = datetime.date.fromisoformat(since)
            except ValueError as exc:
                raise MailProviderError(
                    f"'since' must be an ISO date (YYYY-MM-DD), got {since!r}"
                ) from exc
        return AND(**kwargs) if kwargs else "ALL"

    @staticmethod
    def _from_display(msg) -> str:
        if msg.from_values:
            return msg.from_values.full or msg.from_ or ""
        return msg.from_ or ""

    @classmethod
    def _to_summary(cls, msg, folder: str) -> MessageSummary:
        body = msg.text or (_html_to_text(msg.html) if msg.html else "")
        return MessageSummary(
            uid=msg.uid or "",
            folder=folder,
            subject=msg.subject or "",
            from_addr=cls._from_display(msg),
            to_addrs=list(msg.to),
            date=msg.date_str or None,
            flags=list(msg.flags),
            size=getattr(msg, "size", 0) or 0,
            has_attachments=bool(msg.attachments),
            preview=body[:200].strip(),
        )

    @classmethod
    def _to_detail(cls, msg, folder: str) -> MessageDetail:
        body = msg.text or (_html_to_text(msg.html) if msg.html else "")
        attachments = [
            AttachmentInfo(
                index=i,
                filename=att.filename or f"attachment-{i}",
                content_type=att.content_type or "application/octet-stream",
                size=len(att.payload),
            )
            for i, att in enumerate(msg.attachments)
        ]
        return MessageDetail(
            uid=msg.uid or "",
            folder=folder,
            subject=msg.subject or "",
            from_addr=cls._from_display(msg),
            to_addrs=list(msg.to),
            cc_addrs=list(msg.cc),
            date=msg.date_str or None,
            flags=list(msg.flags),
            message_id=(msg.headers.get("message-id", (None,)) or (None,))[0],
            body_text=body,
            body_length=len(body),
            attachments=attachments,
        )
