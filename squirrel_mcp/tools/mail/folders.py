"""mail_list_folders."""

from __future__ import annotations

from mcp.types import ToolAnnotations

from ...schemas import FolderInfo, FolderList
from .._common import run_blocking


class FoldersToolsMixin:
    """List mailboxes / folders."""

    def _register_folders_tools(self):
        @self.app.tool(
            title="List Folders",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def mail_list_folders() -> FolderList:
            """List all mailboxes/folders in the account.

            Start here to discover the exact folder names to pass as the
            ``folder`` argument of the other tools (e.g. "INBOX", "Drafts",
            "Archive", "Sent").
            """
            provider, sub = await self._get_provider()
            folders = await run_blocking(provider, provider.list_folders)
            self._track_usage(sub, "mail_list_folders")
            return FolderList(
                folders=[
                    FolderInfo(name=f.name, delimiter=f.delimiter, flags=f.flags)
                    for f in folders
                ]
            )
