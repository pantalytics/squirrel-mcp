"""mail_list_accounts, mail_list_folders."""

from __future__ import annotations

from typing import Optional

from mcp.types import ToolAnnotations

from ...schemas import FolderInfo, FolderList, MailAccount, MailAccountList
from .._common import run_blocking


class FoldersToolsMixin:
    """List accounts and mailboxes / folders."""

    def _register_folders_tools(self):
        @self.app.tool(
            title="List Accounts",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        )
        async def mail_list_accounts() -> MailAccountList:
            """List the email accounts this server can read from and send as.

            Every mail tool takes an optional ``account`` argument (the id or
            address returned here). With a single account it can be omitted;
            with several, say explicitly which one you are acting on -- and tell
            the user which address a message goes out from before sending.
            """
            accounts = await self._list_accounts()
            return MailAccountList(
                accounts=[
                    MailAccount(
                        id=str(a.get("id", "")),
                        email=str(a.get("email", "")),
                        default=bool(a.get("default", False)),
                    )
                    for a in accounts
                ]
            )

        @self.app.tool(
            title="List Folders",
            annotations=ToolAnnotations(
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def mail_list_folders(account: Optional[str] = None) -> FolderList:
            """List all mailboxes/folders in the account.

            Start here to discover the exact folder names to pass as the
            ``folder`` argument of the other tools (e.g. "INBOX", "Drafts",
            "Archive", "Sent").

            Args:
                account: Which email account to use (id or address from
                    mail_list_accounts). Omit when only one is configured.
            """
            provider, sub = await self._get_provider(account)
            folders = await run_blocking(provider, provider.list_folders)
            self._track_usage(sub, "mail_list_folders")
            return FolderList(
                folders=[
                    FolderInfo(name=f.name, delimiter=f.delimiter, flags=f.flags)
                    for f in folders
                ]
            )
