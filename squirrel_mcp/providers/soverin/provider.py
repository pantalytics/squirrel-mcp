"""Soverin mail provider: wires the IMAP (read/draft/move) and SMTP (send) clients
together behind the ``MailProvider`` protocol.

Deliberately thin -- the real work lives in ``imap.py`` and ``smtp.py``. This class
exists so the tool layer sees one object that satisfies ``MailProvider``, and so a
future Gmail/Outlook provider has an obvious shape to copy.
"""

from __future__ import annotations

import datetime
from typing import List, Optional, Tuple

from ...config import SquirrelConfig
from ...logging_config import get_logger
from ...search_query import MailQuery
from ..protocol import (
    AttachmentPayload,
    FolderInfo,
    MessageDetail,
    MessageSummary,
    OutgoingAttachment,
)
from .imap import SoverinImapClient
from .smtp import SoverinSmtpClient

logger = get_logger(__name__)


class SoverinMailProvider:
    """MailProvider implementation backed by Soverin IMAP + SMTP over TLS."""

    def __init__(self, config: SquirrelConfig, drafts_folder: str = "Drafts"):
        self._imap = SoverinImapClient(
            host=config.imap_host,
            port=config.imap_port,
            username=config.login_username,
            password=config.mail_password,
            email=config.mail_email,
            drafts_folder=drafts_folder,
            security=config.imap_security,
            tls_verify=config.tls_verify,
        )
        self._smtp = SoverinSmtpClient(
            host=config.smtp_host,
            port=config.smtp_port,
            username=config.login_username,
            password=config.mail_password,
            email=config.mail_email,
            security=config.smtp_security,
            tls_verify=config.tls_verify,
        )
        self._drafts_folder = drafts_folder
        self._email = config.mail_email

    # ---- lifecycle ------------------------------------------------------- #
    @property
    def email(self) -> str:
        return self._email

    @property
    def supports_outgoing_attachments(self) -> bool:
        """MIME can carry anything, so this backend always can."""
        return True

    @property
    def is_authenticated(self) -> bool:
        return self._imap.is_authenticated

    def connect(self) -> None:
        self._imap.connect()

    def authenticate(self) -> None:
        self._imap.authenticate()

    def disconnect(self) -> None:
        self._imap.disconnect()

    # ---- read (IMAP) ----------------------------------------------------- #
    def list_folders(self) -> List[FolderInfo]:
        return self._imap.list_folders()

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
        parsed: Optional[MailQuery] = None,
    ) -> Tuple[List[MessageSummary], int]:
        return self._imap.search(
            folder,
            query,
            unseen_only=unseen_only,
            flagged_only=flagged_only,
            since=since,
            limit=limit,
            offset=offset,
            parsed=parsed,
        )

    def fetch_message(self, folder: str, uid: str) -> MessageDetail:
        return self._imap.fetch_message(folder, uid)

    def fetch_attachment(self, folder: str, uid: str, index: int) -> AttachmentPayload:
        return self._imap.fetch_attachment(folder, uid, index)

    # ---- threading -------------------------------------------------------- #
    def _reply_headers(
        self, reply_to_uid: Optional[str], reply_to_folder: str
    ) -> Tuple[Optional[str], Optional[List[str]]]:
        """Resolve a uid the caller is replying to into threading headers.

        This is the one place the two halves of this provider have to meet: the
        headers that make a reply thread live on the IMAP side, and the message
        that needs them goes out over SMTP.
        """
        if not reply_to_uid:
            return None, None
        return self._imap.reply_headers(reply_to_folder, reply_to_uid)

    # ---- draft / move / flag (IMAP) -------------------------------------- #
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
        in_reply_to, references = self._reply_headers(reply_to_uid, reply_to_folder)
        return self._imap.save_draft(
            to,
            subject,
            body,
            cc=cc,
            bcc=bcc,
            folder=folder,
            in_reply_to=in_reply_to,
            references=references,
            attachments=attachments,
            body_html=body_html,
        )

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
        return self._imap.update_draft(
            folder, uid, to, subject, body, cc=cc, bcc=bcc,
            attachments=attachments, body_html=body_html,
        )

    def move(self, folder: str, uids: List[str], destination: str) -> int:
        return self._imap.move(folder, uids, destination)

    def flag(self, folder: str, uids: List[str], flagged: bool = True) -> int:
        return self._imap.flag(folder, uids, flagged)

    def set_seen(self, folder: str, uids: List[str], seen: bool = True) -> int:
        return self._imap.set_seen(folder, uids, seen)

    # ---- send (SMTP) ----------------------------------------------------- #
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
        in_reply_to, references = self._reply_headers(reply_to_uid, reply_to_folder)
        result = self._smtp.send(
            to,
            subject,
            body,
            cc=cc,
            bcc=bcc,
            in_reply_to=in_reply_to,
            references=references,
            attachments=attachments,
            body_html=body_html,
        )
        # The bytes and the timestamp are for the Sent copy alone and stop
        # here -- the tool layer gets the outcome, not the message back.
        raw = result.pop("raw", b"")
        sent_at = result.pop("sent_at", None)
        saved, folder = self._file_sent_copy(raw, result.get("message_id"), sent_at)
        result["saved_to_sent"] = saved
        result["sent_folder"] = folder
        return result

    def _file_sent_copy(
        self,
        raw: bytes,
        message_id: Optional[str],
        sent_at: Optional[datetime.datetime],
    ) -> Tuple[bool, Optional[str]]:
        """File what SMTP just delivered in the account's Sent folder.

        This is the second thing only the provider can do (``_reply_headers``
        is the first): SMTP has no idea what a folder is and IMAP has no idea
        what was sent, so joining them is exactly this class's job.

        **A failure here must not fail the send.** The message is already with
        the recipient by the time this runs, and raising would report a send
        that happened as one that did not -- which invites a retry and a second
        copy in someone's inbox. So the outcome is carried back in the result
        instead, and the tool layer says whether the copy landed.
        """
        if not raw:
            return False, None
        try:
            folder, appended = self._imap.append_sent(
                raw, message_id=message_id, when=sent_at
            )
        except Exception as exc:  # noqa: BLE001 - the mail is already gone
            logger.warning(
                "Message %s was sent but could not be filed in Sent: %s", message_id, exc
            )
            return False, None
        logger.info(
            "%s %s in %s", "Filed" if appended else "Found", message_id, folder
        )
        return True, folder
