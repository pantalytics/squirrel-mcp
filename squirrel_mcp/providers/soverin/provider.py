"""Soverin mail provider: wires the IMAP (read/draft/move) and SMTP (send) clients
together behind the ``MailProvider`` protocol.

Deliberately thin -- the real work lives in ``imap.py`` and ``smtp.py``. This class
exists so the tool layer sees one object that satisfies ``MailProvider``, and so a
future Gmail/Outlook provider has an obvious shape to copy.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ...config import SquirrelConfig
from ..protocol import (
    AttachmentPayload,
    FolderInfo,
    MessageDetail,
    MessageSummary,
)
from .imap import SoverinImapClient
from .smtp import SoverinSmtpClient


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
    ) -> Tuple[List[MessageSummary], int]:
        return self._imap.search(
            folder,
            query,
            unseen_only=unseen_only,
            flagged_only=flagged_only,
            since=since,
            limit=limit,
            offset=offset,
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
    ) -> str:
        return self._imap.update_draft(folder, uid, to, subject, body, cc=cc, bcc=bcc)

    def move(self, folder: str, uids: List[str], destination: str) -> int:
        return self._imap.move(folder, uids, destination)

    def flag(self, folder: str, uids: List[str], flagged: bool = True) -> int:
        return self._imap.flag(folder, uids, flagged)

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
    ) -> dict:
        in_reply_to, references = self._reply_headers(reply_to_uid, reply_to_folder)
        return self._smtp.send(
            to,
            subject,
            body,
            cc=cc,
            bcc=bcc,
            in_reply_to=in_reply_to,
            references=references,
        )
