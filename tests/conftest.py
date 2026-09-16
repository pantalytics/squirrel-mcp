"""Shared test fixtures: an in-memory fake mail provider."""

from __future__ import annotations

from typing import List, Optional, Tuple

import pytest

from squirrel_mcp.providers.protocol import (
    AttachmentInfo,
    AttachmentPayload,
    FolderInfo,
    MailNotFoundError,
    MessageDetail,
    MessageSummary,
)


class FakeMailProvider:
    """A MailProvider implementation backed by in-memory data (no network)."""

    def __init__(self):
        self.connected = False
        self.sent: list = []
        self.moved: list = []
        self.drafts: list = []
        # Every send/save_draft's keyword arguments, so a test can assert what
        # the tool layer handed the backend (threading in particular).
        self.send_kwargs: list = []
        self.draft_kwargs: list = []
        # Set to mimic a sender who asked for replies somewhere else.
        self.reply_to_addrs: list = []
        # Uids currently carrying \Flagged, so search/fetch report what flag()
        # did and a set-then-clear round trip is actually observable.
        self.flagged: set = set()
        # Flipped off by a test to stand in for a backend that cannot send
        # files, which is what every backend answers by default.
        self.attachments_supported = True
        # Flipped off to stand in for a send whose copy could not be filed --
        # the message still went out, which is the whole point of the field.
        self.sent_copy_ok = True
        # What mail_send_draft sent, as (folder, uid), and the outcome of
        # taking the draft out afterwards -- flipped off to stand in for a
        # delivered message whose draft survived it.
        self.sent_drafts: list = []
        self.draft_removal_ok = True
        # Drafts the fake still holds, so a send-then-look-again is observable.
        self.draft_store: dict = {"900": {"subject": "Reviewed", "to": ["bob@example.com"]}}
        self._email = "me@example.com"

    @property
    def email(self) -> str:
        return self._email

    @property
    def is_authenticated(self) -> bool:
        return self.connected

    @property
    def supports_outgoing_attachments(self) -> bool:
        return self.attachments_supported

    def connect(self) -> None:
        self.connected = True

    def authenticate(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def list_folders(self) -> List[FolderInfo]:
        return [FolderInfo(name="INBOX"), FolderInfo(name="Drafts"), FolderInfo(name="Archive")]

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
        if flagged_only and "101" not in self.flagged:
            return [], 0
        msg = MessageSummary(
            uid="101",
            folder=folder,
            subject="Hello",
            from_addr="Anna <anna@example.com>",
            to_addrs=["me@example.com"],
            date="Mon, 01 Jan 2026 10:00:00 +0000",
            flags=["\\Seen"] + (["\\Flagged"] if "101" in self.flagged else []),
            size=1234,
            has_attachments=True,
            preview="Hi there, this is a preview.",
        )
        return [msg], 1

    def fetch_message(self, folder: str, uid: str) -> MessageDetail:
        return MessageDetail(
            uid=uid,
            folder=folder,
            subject="Hello",
            from_addr="Anna <anna@example.com>",
            to_addrs=["me@example.com", "Bob <bob@example.com>"],
            cc_addrs=["carol@example.com"],
            reply_to_addrs=list(self.reply_to_addrs),
            date="Mon, 01 Jan 2026 10:00:00 +0000",
            flags=["\\Seen"] + (["\\Flagged"] if uid in self.flagged else []),
            message_id="<abc@example.com>",
            body_text="Hello world. " * 100,
            body_length=len("Hello world. " * 100),
            attachments=[
                AttachmentInfo(index=0, filename="doc.pdf", content_type="application/pdf", size=10)
            ],
        )

    def fetch_attachment(self, folder: str, uid: str, index: int) -> AttachmentPayload:
        return AttachmentPayload(
            filename="doc.pdf", content_type="application/pdf", size=5, content=b"%PDF-"
        )

    def save_draft(
        self,
        to,
        subject,
        body,
        *,
        cc=None,
        bcc=None,
        folder="Drafts",
        reply_to_uid=None,
        reply_to_folder="INBOX",
        attachments=None,
        body_html=None,
    ) -> str:
        self.drafts.append((to, subject, body, folder))
        self.draft_kwargs.append(
            {"cc": cc, "bcc": bcc, "reply_to_uid": reply_to_uid,
             "reply_to_folder": reply_to_folder, "attachments": attachments,
             "body_html": body_html}
        )
        return "900"

    def update_draft(
        self, folder, uid, to, subject, body, *, cc=None, bcc=None, attachments=None,
        body_html=None,
    ) -> str:
        self.draft_kwargs.append(
            {"cc": cc, "bcc": bcc, "attachments": attachments, "edit_of": uid,
             "body_html": body_html}
        )
        return "901"

    def send(
        self,
        to,
        subject,
        body,
        *,
        cc=None,
        bcc=None,
        reply_to_uid=None,
        reply_to_folder="INBOX",
        attachments=None,
        body_html=None,
    ) -> dict:
        self.sent.append((to, subject, body))
        self.send_kwargs.append(
            {"cc": cc, "bcc": bcc, "reply_to_uid": reply_to_uid,
             "reply_to_folder": reply_to_folder, "attachments": attachments,
             "body_html": body_html}
        )
        return {
            "message_id": "<sent@example.com>",
            "recipients": list(to) + list(cc or []),
            "saved_to_sent": self.sent_copy_ok,
            "sent_folder": "Sent" if self.sent_copy_ok else None,
        }

    def send_draft(self, folder, uid) -> dict:
        if uid not in self.draft_store:
            raise MailNotFoundError(f"Draft uid {uid} not found in {folder}")
        draft = self.draft_store[uid]
        self.sent_drafts.append((folder, uid))
        if self.draft_removal_ok:
            del self.draft_store[uid]
        return {
            "message_id": "<draft-sent@example.com>",
            "recipients": list(draft["to"]),
            "subject": draft["subject"],
            "attachments": ["doc.pdf"],
            "saved_to_sent": self.sent_copy_ok,
            "sent_folder": "Sent" if self.sent_copy_ok else None,
            "draft_removed": self.draft_removal_ok,
        }

    def move(self, folder, uids, destination) -> int:
        self.moved.append((folder, uids, destination))
        return len(uids)

    def flag(self, folder, uids, flagged: bool = True) -> int:
        for uid in uids:
            if flagged:
                self.flagged.add(uid)
            else:
                self.flagged.discard(uid)
        return len(uids)


@pytest.fixture
def fake_provider() -> FakeMailProvider:
    p = FakeMailProvider()
    p.connect()
    return p
