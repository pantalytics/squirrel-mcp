"""Build RFC-822 messages, shared by the SMTP (send) and IMAP (draft) paths."""

from __future__ import annotations

import email.utils
from email.message import EmailMessage
from typing import List, Optional

# How many parent Message-IDs a References header carries. RFC 5322 §3.6.4 lets
# a client trim a long chain, and every mail client does -- a decade-old thread
# would otherwise put kilobytes of header on every reply. Keeping the *first*
# entry (the thread root) plus the most recent ones is the conventional trim:
# clients thread on the nearest ancestor they recognise, and the root is what
# ties the whole conversation together.
MAX_REFERENCES = 20


def build_email(
    from_addr: str,
    to: List[str],
    subject: str,
    body: str,
    *,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    message_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[List[str]] = None,
) -> EmailMessage:
    """Assemble a plain-text EmailMessage with a stable Message-ID.

    The Message-ID lets us find a freshly appended draft back on the IMAP server
    (many servers don't return APPENDUID reliably).

    ``in_reply_to`` / ``references`` are what make a reply land *in* the thread
    rather than next to it. Without them a mail client has only the subject and
    the participants to go on, which Gmail will usually guess right and Outlook
    usually will not.
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
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        # Folded by the email package on send; one id per line keeps the header
        # inside the 998-octet line limit for a long thread.
        msg["References"] = " ".join(references)
    msg.set_content(body or "")
    return msg


def parse_references(raw: Optional[str]) -> List[str]:
    """A raw References/In-Reply-To header -> the message-ids in it, in order.

    The header is whitespace-separated ``<id>`` tokens in theory; in practice
    it also arrives comma-separated (Outlook) or wrapped over several lines.
    Anything not shaped like a message-id is dropped rather than guessed at.
    """
    if not raw:
        return []
    tokens = raw.replace(",", " ").split()
    seen: List[str] = []
    for token in tokens:
        token = token.strip()
        if token.startswith("<") and token.endswith(">") and token not in seen:
            seen.append(token)
    return seen


def reply_chain(
    parent_message_id: Optional[str], parent_references: Optional[List[str]]
) -> List[str]:
    """The References a reply to that parent should carry.

    The parent's own chain plus the parent itself, de-duplicated and trimmed to
    ``MAX_REFERENCES`` from both ends (thread root first, nearest ancestors
    last) -- see the constant.
    """
    chain = [ref for ref in (parent_references or []) if ref]
    if parent_message_id and parent_message_id not in chain:
        chain.append(parent_message_id)
    if len(chain) <= MAX_REFERENCES:
        return chain
    return chain[:1] + chain[-(MAX_REFERENCES - 1) :]


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
