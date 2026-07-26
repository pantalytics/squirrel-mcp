"""mail_draft, mail_edit_draft, mail_send."""

from __future__ import annotations

from typing import Any, Optional

from mcp.types import ToolAnnotations

from ...error_handling import ValidationError
from ...schemas import DraftResult, SendResult
from .._common import as_str_list, require_confirm, run_blocking


class ComposeToolsMixin:
    """Create/update drafts and send mail (send requires confirmation)."""

    def _register_compose_tools(self):
        @self.app.tool(
            title="Draft Mail",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_draft(
            to: Any,
            subject: str,
            body: str,
            cc: Optional[Any] = None,
            bcc: Optional[Any] = None,
            folder: str = "Drafts",
            account: Optional[str] = None,
        ) -> DraftResult:
            """Save a new draft to the Drafts folder (nothing is sent).

            ``to``/``cc``/``bcc`` accept a list of addresses or a comma-separated
            string. Prefer drafting and letting the user review over sending directly.
            ``account`` picks which configured email account the draft belongs to
            (id or address from mail_list_accounts); omit with a single account.
            """
            provider, sub = await self._get_provider(account, writes=True)
            recipients = as_str_list(to)
            if not recipients:
                raise ValidationError("'to' is required (at least one recipient)")
            uid = await run_blocking(
                provider,
                provider.save_draft,
                recipients,
                subject,
                body,
                cc=as_str_list(cc),
                bcc=as_str_list(bcc),
                folder=folder,
            )
            self._track_usage(sub, "mail_draft")
            return DraftResult(
                uid=uid,
                folder=folder,
                status="Draft saved",
                from_address=getattr(provider, "email", None) or None,
            )

        @self.app.tool(
            title="Edit Draft",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_edit_draft(
            uid: str,
            to: Any,
            subject: str,
            body: str,
            cc: Optional[Any] = None,
            bcc: Optional[Any] = None,
            folder: str = "Drafts",
            account: Optional[str] = None,
        ) -> DraftResult:
            """Replace an existing draft with new content. Returns the new uid.

            (The draft is rewritten, so the uid changes -- use the returned one.)
            Pass the same ``account`` the draft was created in.
            """
            provider, sub = await self._get_provider(account, writes=True)
            recipients = as_str_list(to)
            if not recipients:
                raise ValidationError("'to' is required (at least one recipient)")
            new_uid = await run_blocking(
                provider,
                provider.update_draft,
                folder,
                uid,
                recipients,
                subject,
                body,
                cc=as_str_list(cc),
                bcc=as_str_list(bcc),
            )
            self._track_usage(sub, "mail_edit_draft")
            return DraftResult(
                uid=new_uid,
                folder=folder,
                status="Draft updated",
                from_address=getattr(provider, "email", None) or None,
            )

        @self.app.tool(
            title="Send Mail",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_send(
            to: Any,
            subject: str,
            body: str,
            cc: Optional[Any] = None,
            bcc: Optional[Any] = None,
            confirm: bool = False,
            account: Optional[str] = None,
        ) -> SendResult:
            """Send a message. OUTGOING -- requires confirm=true.

            Before calling with confirm=true, show the user the exact recipients,
            subject and body and get explicit approval. Without confirm=true this
            refuses and sends nothing. ``account`` picks which configured email
            account to send from (id or address from mail_list_accounts) -- name
            it to the user as part of the approval; omit with a single account.
            """
            provider, sub = await self._get_provider(account, writes=True)
            recipients = as_str_list(to)
            if not recipients:
                raise ValidationError("'to' is required (at least one recipient)")
            require_confirm(confirm, "Sending mail")
            result = await run_blocking(
                provider,
                provider.send,
                recipients,
                subject,
                body,
                cc=as_str_list(cc),
                bcc=as_str_list(bcc),
            )
            self._track_usage(sub, "mail_send")
            return SendResult(
                status="Sent",
                message_id=result.get("message_id"),
                recipients=result.get("recipients", []),
                from_address=getattr(provider, "email", None) or None,
            )
