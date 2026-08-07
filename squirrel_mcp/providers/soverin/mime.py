"""Build RFC-822 messages, shared by the SMTP (send) and IMAP (draft) paths."""

from __future__ import annotations

import email.utils
from email.message import EmailMessage
from typing import List, Optional

from ..protocol import OutgoingAttachment

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
    attachments: Optional[List[OutgoingAttachment]] = None,
    body_html: Optional[str] = None,
) -> EmailMessage:
    """Assemble an EmailMessage with a stable Message-ID.

    The Message-ID lets us find a freshly appended draft back on the IMAP server
    (many servers don't return APPENDUID reliably).

    ``in_reply_to`` / ``references`` are what make a reply land *in* the thread
    rather than next to it. Without them a mail client has only the subject and
    the participants to go on, which Gmail will usually guess right and Outlook
    usually will not.

    ``attachments`` is where "don't reinvent the wheel" pays for itself: neither
    SMTP nor IMAP knows what an attachment is -- both carry one opaque RFC 5322
    blob -- so a file is purely a MIME concern, and ``add_attachment`` already
    handles all of it. It promotes the message to ``multipart/mixed``, picks
    base64 (SMTP is 7-bit with a 998-octet line limit, so binary has no other
    way across), and encodes a non-ASCII filename per RFC 2231. Which is why
    this one function is the whole of it for both the SMTP and the IMAP path.

    ``body_html`` is added as an *alternative*, never a replacement: ``body``
    stays the text a plain-text client shows, and both halves are the same
    message. It is also what makes an ``inline`` attachment mean anything --
    see ``_add_parts``, which is where a part becomes an embedded image rather
    than a second paperclip.
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
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    _add_parts(msg, attachments or [], has_html=bool(body_html))
    return msg


def _add_parts(
    msg: EmailMessage, attachments: List[OutgoingAttachment], *, has_html: bool
) -> None:
    """Hang the attachments off a message that already carries its body.

    Where each part goes is what decides whether an inline image *shows*:

    * an inline part belongs inside ``multipart/related`` next to the HTML that
      refers to it, which is what ``add_related`` on the HTML part builds. A
      client resolves ``cid:`` within the related group, so the same image
      parked in the outer ``multipart/mixed`` alongside the ordinary
      attachments renders in some clients and arrives as a second paperclip in
      the rest;
    * everything else is ``add_attachment``, which promotes the message to
      ``multipart/mixed`` -- the ordinary paperclip.

    Inline with no HTML body to refer to it is the case with nowhere good to
    go, and it is the reason this stayed out of the first version: it becomes
    an ordinary attachment, keeping its ``Content-ID`` so a client that wants
    to show it still can. Degrading is the honest half of the promise -- an
    image nothing points at is an attachment whatever the caller called it.
    """
    for att in attachments:
        maintype, _, subtype = (att.content_type or "").partition("/")
        # A part needs both halves of a type. Anything we cannot split is
        # treated as opaque bytes, which is what every client does with a type
        # it does not recognise anyway.
        if not maintype or not subtype:
            maintype, subtype = "application", "octet-stream"
        cid = f"<{att.content_id}>" if att.content_id else None
        if att.inline and has_html:
            # The HTML is the last part of the alternative the body built.
            msg.get_payload()[-1].add_related(
                att.content,
                maintype=maintype,
                subtype=subtype,
                cid=cid,
                filename=att.filename,
                disposition="inline",
            )
            continue
        msg.add_attachment(
            att.content,
            maintype=maintype,
            subtype=subtype,
            filename=att.filename,
            cid=cid,
            disposition="inline" if att.inline else "attachment",
        )


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
