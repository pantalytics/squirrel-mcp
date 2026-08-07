"""Turn an ``attachments`` tool argument into bytes the provider can send.

Two ways to name a file, and the choice between them is the whole design here.

* ``{"source_uid": "412", "source_index": 0}`` -- re-attach a file that is
  already in the mailbox. Nothing crosses the wire twice: the provider fetches
  the part over IMAP and hands it straight back. Forwarding an invoice is the
  common case by a wide margin, and this is the only form that does not put the
  file through the model's context on the way past.
* ``{"filename": "x.pdf", "content_base64": "..."}`` -- bytes from the client.
  The universal form, and the expensive one: base64 inflates by a third and
  every byte is a token the model had to emit, so a megabyte of PDF is several
  hundred thousand tokens. Fine for something small and generated, wasteful for
  anything the mailbox already holds.

Either form takes ``"inline": true`` (plus an optional ``"content_id"``) to
embed an image in the body rather than hang it off the message. That only
renders against a ``body_html`` referring to the part as ``cid:<id>``; without
one it stays an ordinary attachment, which is the honest outcome rather than a
``multipart/related`` nothing points into.

There is deliberately **no file path**. In the local single-mailbox deployment
it would be the obvious third option, but the hosted multi-tenant server shares
this exact tool layer, and a path there is an arbitrary read of the *server's*
filesystem by any authenticated tenant. One argument meaning two different
things depending on deployment is exactly the divergence the two backends are
otherwise held to, so it does not exist in either.
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
import re
from typing import Any, List, Optional

from ...error_handling import ValidationError
from ...providers.protocol import OutgoingAttachment
from .._common import MAX_OUTGOING_TOTAL_BYTES, run_blocking

# Everything a Content-ID must not contain. It travels inside angle brackets in
# a header and is matched against a ``cid:`` URL in the HTML body, so anything
# needing quoting or escaping is replaced rather than sent and hoped for.
_CID_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _guess_content_type(filename: str, given: Optional[str]) -> str:
    if given and "/" in given:
        return given
    guessed, _encoding = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


def _content_id(given: Any, filename: str, index: int) -> str:
    """Normalise a caller's content id, or invent one from the filename.

    A model writes the same id three ways -- bare (``logo``), as the URL it put
    in the body (``cid:logo``), or as the header spelling (``<logo>``) -- and
    all three mean one part. They reduce to the bare form here, which is what
    the HTML's ``cid:`` names and what the header wraps in brackets. An inline
    part with no id at all still gets one: a part nothing can name is a part
    nothing can show.
    """
    raw = str(given or "").strip()
    if raw.lower().startswith("cid:"):
        raw = raw[4:]
    cleaned = _CID_UNSAFE.sub("-", raw.strip("<>").strip()).strip("-")
    if cleaned:
        return cleaned
    stem = _CID_UNSAFE.sub("-", (filename or "").rsplit(".", 1)[0]).strip("-")
    return f"{stem or 'inline'}-{index}"


def _decode(spec_index: int, raw: Any) -> bytes:
    if not isinstance(raw, str):
        raise ValidationError(
            f"attachments[{spec_index}].content_base64 must be a base64 string"
        )
    try:
        # validate=True so a stray data: prefix or accidental prose fails here,
        # loudly, rather than becoming a corrupt file the recipient opens.
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(
            f"attachments[{spec_index}].content_base64 is not valid base64 ({exc})"
        ) from exc


async def resolve_attachments(
    provider: Any,
    specs: Any,
    *,
    default_folder: str = "INBOX",
) -> List[OutgoingAttachment]:
    """Validate the tool argument and return what the provider should attach.

    Returns ``[]`` for ``None`` or an empty list, so callers can pass the result
    straight through -- except ``mail_edit_draft``, where "not mentioned" has to
    stay distinguishable from "attach nothing" (see ``update_draft``).
    """
    if specs is None:
        return []
    if isinstance(specs, dict):
        specs = [specs]
    if not isinstance(specs, (list, tuple)):
        raise ValidationError(
            "'attachments' must be a list of objects, each with either "
            "filename + content_base64, or source_uid + source_index"
        )
    if not specs:
        return []

    # Ask before accepting a single byte. A backend that quietly ignored the
    # kwarg would send the message *without* the file and report success, and
    # a recipient who never got the attachment is a worse outcome than a tool
    # call that refused. Backends predating attachments answer False here.
    if not getattr(provider, "supports_outgoing_attachments", False):
        raise ValidationError(
            "This account's mail backend cannot send attachments yet. Send the "
            "message without them, or use an IMAP/SMTP account."
        )

    resolved: List[OutgoingAttachment] = []
    for i, spec in enumerate(specs):
        if not isinstance(spec, dict):
            raise ValidationError(
                f"attachments[{i}] must be an object, got {type(spec).__name__}"
            )

        source_uid = spec.get("source_uid")
        # "inline" is the caller's intent; whether it is honoured depends on
        # there being an HTML body, which only the MIME layer can see.
        inline = bool(spec.get("inline"))
        has_inline = spec.get("content_base64") is not None
        if source_uid and has_inline:
            raise ValidationError(
                f"attachments[{i}] sets both source_uid and content_base64 -- pick one"
            )

        if source_uid:
            index = spec.get("source_index", 0)
            try:
                index = int(index)
            except (TypeError, ValueError):
                raise ValidationError(
                    f"attachments[{i}].source_index must be a number"
                ) from None
            payload = await run_blocking(
                provider,
                provider.fetch_attachment,
                str(spec.get("source_folder") or default_folder),
                str(source_uid),
                index,
            )
            filename = str(spec.get("filename") or payload.filename)
            resolved.append(
                OutgoingAttachment(
                    filename=filename,
                    content_type=_guess_content_type(payload.filename, payload.content_type),
                    content=payload.content,
                    inline=inline,
                    content_id=(
                        _content_id(spec.get("content_id"), filename, i) if inline else None
                    ),
                )
            )
        elif has_inline:
            filename = str(spec.get("filename") or "").strip()
            if not filename:
                raise ValidationError(
                    f"attachments[{i}].filename is required -- it is what the "
                    f"recipient sees and what their mail client opens it with"
                )
            content = _decode(i, spec.get("content_base64"))
            resolved.append(
                OutgoingAttachment(
                    filename=filename,
                    content_type=_guess_content_type(filename, spec.get("content_type")),
                    content=content,
                    inline=inline,
                    content_id=(
                        _content_id(spec.get("content_id"), filename, i) if inline else None
                    ),
                )
            )
        else:
            raise ValidationError(
                f"attachments[{i}] needs either source_uid (+ source_index) to "
                f"re-send a file already in the mailbox, or filename + content_base64"
            )

    total = sum(a.size for a in resolved)
    if total > MAX_OUTGOING_TOTAL_BYTES:
        raise ValidationError(
            f"Attachments total {total / 1_048_576:.1f} MB, over the "
            f"{MAX_OUTGOING_TOTAL_BYTES / 1_048_576:.0f} MB limit. Most receiving "
            f"servers refuse more than that anyway -- send a link instead."
        )
    return resolved
