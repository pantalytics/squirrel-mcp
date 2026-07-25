"""Build RFC-822 messages, shared by the SMTP (send) and IMAP (draft) paths."""

from __future__ import annotations

import email.utils
from email.message import EmailMessage
from typing import List, Optional


def build_email(
    from_addr: str,
    to: List[str],
    subject: str,
    body: str,
    *,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    message_id: Optional[str] = None,
) -> EmailMessage:
    """Assemble a plain-text EmailMessage with a stable Message-ID.

    The Message-ID lets us find a freshly appended draft back on the IMAP server
    (many servers don't return APPENDUID reliably).
    """
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        # Kept for the SMTP envelope; callers strip this header before storing
        # a draft so BCC recipients never leak into the saved copy.
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    domain = from_addr.split("@")[-1] if "@" in from_addr else "squirrel.local"
    msg["Message-ID"] = message_id or email.utils.make_msgid(domain=domain)
    msg.set_content(body or "")
    return msg


def all_recipients(
    to: List[str],
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
) -> List[str]:
    """Flatten to/cc/bcc into the SMTP envelope recipient list (de-duplicated)."""
    seen: List[str] = []
    for group in (to, cc or [], bcc or []):
        for addr in group:
            if addr and addr not in seen:
                seen.append(addr)
    return seen
