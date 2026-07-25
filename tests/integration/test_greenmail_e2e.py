"""End-to-end tests against a real GreenMail IMAP/SMTP server.

These exercise Squirrel's actual provider path (imap-tools + smtplib) -- no mocks,
no Soverin credentials. Marked ``integration`` so the default unit run skips them.
"""

from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage

import pytest
from imap_tools import MailBoxUnencrypted

from squirrel_mcp.providers import create_mail_provider

pytestmark = pytest.mark.integration

TEST_EMAIL = "squirrel@example.com"
TEST_LOGIN = "squirrel"
TEST_PASSWORD = "squirrelpass"


def _ensure_folders(host: str, imap_port: int, *names: str) -> None:
    mb = MailBoxUnencrypted(host, port=imap_port).login(TEST_LOGIN, TEST_PASSWORD)
    try:
        existing = {f.name for f in mb.folder.list()}
        for name in names:
            if name not in existing:
                mb.folder.create(name)
    finally:
        mb.logout()


def _seed_message(host: str, smtp_port: int, subject: str, body: str, attachment: bool = False):
    msg = EmailMessage()
    msg["From"] = "sender@external.test"
    msg["To"] = TEST_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    if attachment:
        msg.add_attachment(
            b"%PDF-1.4 fake pdf bytes",
            maintype="application",
            subtype="pdf",
            filename="report.pdf",
        )
    with smtplib.SMTP(host, smtp_port, timeout=15) as s:
        s.send_message(msg)


@pytest.fixture
def provider(greenmail):
    _ensure_folders(greenmail.imap_host, greenmail.imap_port, "Drafts", "Archive")
    p = create_mail_provider(greenmail)
    p.connect()
    yield p, greenmail
    p.disconnect()


def _find_by_subject(messages, subject):
    return next((m for m in messages if m.subject == subject), None)


def test_list_search_read(provider):
    p, cfg = provider
    subject = f"E2E read {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "Hello from GreenMail body.")

    folders = {f.name for f in p.list_folders()}
    assert "INBOX" in folders

    messages, total = p.search("INBOX", limit=50)
    assert total >= 1
    found = _find_by_subject(messages, subject)
    assert found is not None, "seeded message not found in INBOX"

    detail = p.fetch_message("INBOX", found.uid)
    assert "Hello from GreenMail" in detail.body_text
    assert detail.subject == subject


def test_attachment_roundtrip(provider):
    p, cfg = provider
    subject = f"E2E attach {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "See attached.", attachment=True)

    messages, _ = p.search("INBOX", limit=50)
    found = _find_by_subject(messages, subject)
    assert found is not None and found.has_attachments

    detail = p.fetch_message("INBOX", found.uid)
    assert len(detail.attachments) == 1
    assert detail.attachments[0].filename == "report.pdf"

    payload = p.fetch_attachment("INBOX", found.uid, 0)
    assert payload.content.startswith(b"%PDF")
    assert payload.filename == "report.pdf"


def test_draft_save_and_find(provider):
    p, cfg = provider
    subject = f"E2E draft {uuid.uuid4().hex[:8]}"

    uid = p.save_draft(["someone@example.com"], subject, "Draft body", folder="Drafts")
    assert uid  # server returned the appended uid

    drafts, _ = p.search("Drafts", limit=50)
    assert _find_by_subject(drafts, subject) is not None


def test_move_between_folders(provider):
    p, cfg = provider
    subject = f"E2E move {uuid.uuid4().hex[:8]}"
    _seed_message(cfg.smtp_host, cfg.smtp_port, subject, "Move me.")

    inbox, _ = p.search("INBOX", limit=50)
    target = _find_by_subject(inbox, subject)
    assert target is not None

    moved = p.move("INBOX", [target.uid], "Archive")
    assert moved == 1

    archive, _ = p.search("Archive", limit=50)
    assert _find_by_subject(archive, subject) is not None


def test_send_delivers_to_self(provider):
    p, cfg = provider
    subject = f"E2E send {uuid.uuid4().hex[:8]}"

    result = p.send([TEST_EMAIL], subject, "Sent via Squirrel over SMTP.")
    assert TEST_EMAIL in result["recipients"]

    inbox, _ = p.search("INBOX", limit=50)
    assert _find_by_subject(inbox, subject) is not None
