"""Shared test fixtures: an in-memory fake mail provider."""

from __future__ import annotations

from typing import List, Optional, Tuple

import pytest

from squirrel_mcp.providers.protocol import (
    AttachmentInfo,
    AttachmentPayload,
    FolderInfo,
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
        self._email = "me@example.com"

    @property
    def email(self) -> str:
        return self._email

    @property
    def is_authenticated(self) -> bool:
        return self.connected

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
        since: Optional[str] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[MessageSummary], int]:
        msg = MessageSummary(
            uid="101",
            folder=folder,
            subject="Hello",
            from_addr="Anna <anna@example.com>",
            to_addrs=["me@example.com"],
            date="Mon, 01 Jan 2026 10:00:00 +0000",
            flags=["\\Seen"],
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
            to_addrs=["me@example.com"],
            cc_addrs=[],
            date="Mon, 01 Jan 2026 10:00:00 +0000",
            flags=["\\Seen"],
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

    def save_draft(self, to, subject, body, *, cc=None, bcc=None, folder="Drafts") -> str:
        self.drafts.append((to, subject, body, folder))
        return "900"

    def update_draft(self, folder, uid, to, subject, body, *, cc=None, bcc=None) -> str:
        return "901"

    def send(self, to, subject, body, *, cc=None, bcc=None) -> dict:
        self.sent.append((to, subject, body))
        return {"message_id": "<sent@example.com>", "recipients": list(to)}

    def move(self, folder, uids, destination) -> int:
        self.moved.append((folder, uids, destination))
        return len(uids)


@pytest.fixture
def fake_provider() -> FakeMailProvider:
    p = FakeMailProvider()
    p.connect()
    return p
