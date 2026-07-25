"""mail_search -- paginated search over one folder."""

from __future__ import annotations

from typing import Optional

from mcp.types import ToolAnnotations

from ...schemas import MessageSummary, SearchResult
from .._common import run_blocking


class QueryToolsMixin:
    """Search a folder, newest first, with pagination for large mailboxes."""

    def _register_query_tools(self):
        @self.app.tool(
            title="Search Mail",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def mail_search(
            query: Optional[str] = None,
            folder: str = "INBOX",
            limit: Optional[int] = None,
            offset: int = 0,
            unseen_only: bool = False,
            since: Optional[str] = None,
        ) -> SearchResult:
            """Search one folder, newest first.

            Args:
                query: Free-text to match against the message (subject/body/headers).
                    Omit to list the folder.
                folder: Folder to search (default "INBOX"). Get names from
                    mail_list_folders.
                limit: Page size. Defaults to the server default, capped at the max.
                offset: Number of messages to skip (for paging).
                unseen_only: If true, only unread messages.
                since: ISO date (YYYY-MM-DD) lower bound on the message date.

            The mailbox can be large -- page with limit/offset rather than pulling
            everything. ``total`` tells you how many match in this folder.
            """
            provider, sub = await self._get_provider()
            default_limit = self.config.default_limit if self.config else 25
            max_limit = self.config.max_limit if self.config else 100
            eff_limit = default_limit if limit is None else limit
            eff_limit = max(1, min(eff_limit, max_limit))
            eff_offset = max(0, offset)

            messages, total = await run_blocking(
                provider,
                provider.search,
                folder,
                query,
                unseen_only=unseen_only,
                since=since,
                limit=eff_limit,
                offset=eff_offset,
            )
            self._track_usage(sub, "mail_search")
            return SearchResult(
                messages=[
                    MessageSummary(
                        uid=m.uid,
                        folder=m.folder,
                        subject=m.subject,
                        from_addr=m.from_addr,
                        to_addrs=m.to_addrs,
                        date=m.date,
                        flags=m.flags,
                        size=m.size,
                        has_attachments=m.has_attachments,
                        preview=m.preview,
                    )
                    for m in messages
                ],
                total=total,
                limit=eff_limit,
                offset=eff_offset,
                folder=folder,
            )
