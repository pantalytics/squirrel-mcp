"""mail_move -- move messages between folders (requires confirmation)."""

from __future__ import annotations

from typing import Any, Optional

from mcp.types import ToolAnnotations

from ...error_handling import ValidationError
from ...schemas import MoveResult
from .._common import as_str_list, require_confirm, run_blocking


class OrganizeToolsMixin:
    """Move messages between folders."""

    def _register_organize_tools(self):
        @self.app.tool(
            title="Move Mail",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_move(
            uids: Any,
            source_folder: str,
            destination_folder: str,
            confirm: bool = False,
            account: Optional[str] = None,
        ) -> MoveResult:
            """Move one or more messages between folders. Requires confirm=true.

            ``uids`` accepts a list or a comma-separated string (uids come from
            mail_search in ``source_folder``). Verify ``destination_folder`` exists
            via mail_list_folders and confirm with the user before confirm=true.
            Pass the same ``account`` the uids came from (see mail_list_accounts).
            """
            provider, sub = await self._get_provider(account, writes=True)
            uid_list = as_str_list(uids)
            if not uid_list:
                raise ValidationError("'uids' is required (at least one message uid)")
            require_confirm(confirm, "Moving mail")
            moved = await run_blocking(
                provider, provider.move, source_folder, uid_list, destination_folder
            )
            self._track_usage(sub, "mail_move")
            return MoveResult(
                moved=moved,
                source_folder=source_folder,
                destination_folder=destination_folder,
                uids=uid_list,
            )
