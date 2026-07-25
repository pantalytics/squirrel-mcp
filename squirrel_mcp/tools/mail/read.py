"""mail_read, mail_read_chunk, mail_get_attachment."""

from __future__ import annotations

import base64

from mcp.types import ToolAnnotations

from ...error_handling import ValidationError
from ...schemas import AttachmentContent, AttachmentMeta, MailBody, MailChunk
from .._common import MAX_ATTACHMENT_BYTES, run_blocking


class ReadToolsMixin:
    """Read a message, page a large body, download an attachment."""

    def _register_read_tools(self):
        @self.app.tool(
            title="Read Mail",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def mail_read(uid: str, folder: str = "INBOX") -> MailBody:
            """Read one message by uid (from mail_search).

            Large bodies are truncated: ``is_truncated`` tells you whether to page
            the rest with mail_read_chunk. ``attachments`` lists downloadable parts
            by index (use mail_get_attachment). Does not mark the message read.
            """
            provider, sub = await self._get_provider()
            max_chars = self.config.max_body_chars if self.config else 20000
            detail = await run_blocking(provider, provider.fetch_message, folder, uid)
            self._track_usage(sub, "mail_read")

            is_truncated = detail.body_length > max_chars
            return MailBody(
                uid=detail.uid,
                folder=detail.folder,
                subject=detail.subject,
                from_addr=detail.from_addr,
                to_addrs=detail.to_addrs,
                cc_addrs=detail.cc_addrs,
                date=detail.date,
                flags=detail.flags,
                message_id=detail.message_id,
                body=detail.body_text[:max_chars],
                body_length=detail.body_length,
                is_truncated=is_truncated,
                attachments=[
                    AttachmentMeta(
                        index=a.index,
                        filename=a.filename,
                        content_type=a.content_type,
                        size=a.size,
                    )
                    for a in detail.attachments
                ],
            )

        @self.app.tool(
            title="Read Mail Chunk",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def mail_read_chunk(
            uid: str,
            folder: str = "INBOX",
            offset: int = 0,
            length: int = 20000,
        ) -> MailChunk:
            """Fetch a slice of a large message body.

            Use after mail_read reports ``is_truncated``. Advance ``offset`` by the
            returned ``length`` until ``has_more`` is false.
            """
            provider, sub = await self._get_provider()
            length = max(1, length)
            offset = max(0, offset)
            detail = await run_blocking(provider, provider.fetch_message, folder, uid)
            self._track_usage(sub, "mail_read_chunk")

            full = detail.body_text
            chunk = full[offset : offset + length]
            return MailChunk(
                uid=detail.uid,
                folder=detail.folder,
                chunk=chunk,
                offset=offset,
                length=len(chunk),
                body_length=len(full),
                has_more=offset + len(chunk) < len(full),
            )

        @self.app.tool(
            title="Get Attachment",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def mail_get_attachment(
            uid: str,
            folder: str = "INBOX",
            attachment_index: int = 0,
        ) -> AttachmentContent:
            """Download one attachment (base64) by its index from mail_read."""
            provider, sub = await self._get_provider()
            payload = await run_blocking(
                provider, provider.fetch_attachment, folder, uid, attachment_index
            )
            self._track_usage(sub, "mail_get_attachment")

            if payload.size > MAX_ATTACHMENT_BYTES:
                raise ValidationError(
                    f"Attachment is {payload.size} bytes, over the "
                    f"{MAX_ATTACHMENT_BYTES}-byte inline limit."
                )
            return AttachmentContent(
                filename=payload.filename,
                content_type=payload.content_type,
                size=payload.size,
                data_base64=base64.b64encode(payload.content).decode("ascii"),
            )
