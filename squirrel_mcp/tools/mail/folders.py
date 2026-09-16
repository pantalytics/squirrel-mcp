"""mail_list_accounts, mail_list_folders, and the three that write folders.

A folder's ``role`` is the one thing here that is not just a passthrough. The
names are localized and a caller cannot know them, so "move it to Archive" was
a guess that failed on every non-English mailbox; the server already says which
folder it means as the archive (RFC 6154 SPECIAL-USE), and this reports it.

The same not-knowable spelling is why ``mail_create_folder`` takes a ``parent``
rather than a path: "/" or "." is the server's business. And it is why
``mail_delete_folder`` is the strictest tool in this package -- deleting a
folder takes its messages with it and no Trash catches them, so an empty folder
is the only one it will delete, and the confirm gate sits on top of that rather
than instead of it.
"""

from __future__ import annotations

from typing import Optional

from mcp.types import ToolAnnotations

from ...error_handling import ValidationError
from ...providers.protocol import folder_role
from ...schemas import (
    FolderCreateResult,
    FolderDeleteResult,
    FolderInfo,
    FolderList,
    FolderRenameResult,
    MailAccount,
    MailAccountList,
)
from .._common import require_confirm, run_blocking


class FoldersToolsMixin:
    """List accounts and mailboxes / folders, and create, rename or delete one."""

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

            Each folder carries a ``role`` when the server declares one: sent,
            trash, archive, junk or drafts. USE IT rather than guessing an
            English name -- a Dutch mailbox archives into "Archief" and files
            sent mail in "Verzonden items", and the role is how you know which
            is which. To archive a message, move it to the folder whose role is
            "archive"; to put an archived one back, move it to INBOX.

            Args:
                account: Which email account to use (id or address from
                    mail_list_accounts). Omit when only one is configured.
            """
            provider, sub = await self._get_provider(account)
            folders = await run_blocking(provider, provider.list_folders)
            self._track_usage(sub, "mail_list_folders")
            return FolderList(
                folders=[
                    FolderInfo(
                        name=f.name,
                        delimiter=f.delimiter,
                        flags=f.flags,
                        role=folder_role(f.flags),
                    )
                    for f in folders
                ]
            )

        @self.app.tool(
            title="Create Folder",
            annotations=ToolAnnotations(
                # A new folder holds nothing and deleting it puts the mailbox
                # back exactly as it was, so there is nothing to gate.
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def mail_create_folder(
            name: str,
            parent: Optional[str] = None,
            account: Optional[str] = None,
        ) -> FolderCreateResult:
            """Make a new folder to file mail into. No confirmation needed.

            Use it when the user asks for mail to go somewhere that does not
            exist yet ("put these in a folder called Belastingdienst"), then
            mail_move the messages into the ``folder`` this returns.

            Args:
                name: The folder's own name, NOT a path -- pass "Belastingdienst",
                    never "INBOX/Belastingdienst". The server's delimiter is "/"
                    on one host and "." on the next and is not yours to type.
                parent: An existing folder (from mail_list_folders) to create it
                    inside. Omit for a top-level folder. If the server refuses a
                    top-level folder, this mailbox keeps everything under INBOX:
                    try again with parent="INBOX".
                account: Which email account (id or address from
                    mail_list_accounts). Omit when only one is configured.

            ``created: false`` means the folder was already there and nothing
            changed -- report that rather than a new folder. Always use the
            returned ``folder`` as the destination of a later mail_move: a child
            folder's real name carries its parent.
            """
            provider, sub = await self._get_provider(account, writes=True)
            create = getattr(provider, "create_folder", None)
            if create is None:
                raise ValidationError(
                    "This mail account's backend cannot create folders. Make the "
                    "folder in a mail client, then mail_move into it."
                )
            if not (name or "").strip():
                raise ValidationError("'name' is required (the folder's name)")
            folder, created = await run_blocking(provider, create, name, parent)
            self._track_usage(sub, "mail_create_folder")
            return FolderCreateResult(folder=folder, created=created)

        @self.app.tool(
            title="Rename Folder",
            annotations=ToolAnnotations(
                # Reversible by renaming it back, and it loses nothing: the
                # messages and any sub-folders travel with it.
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_rename_folder(
            name: str,
            new_name: str,
            account: Optional[str] = None,
        ) -> FolderRenameResult:
            """Rename a folder. Its mail and sub-folders come along.

            Args:
                name: The folder's current full name, exactly as
                    mail_list_folders reports it.
                new_name: What to call it -- the name alone, not a path. The
                    folder stays where it is in the hierarchy.
                account: Which email account (id or address from
                    mail_list_accounts). Omit when only one is configured.

            INBOX and the folders with a ``role`` (sent, trash, archive, junk,
            drafts) are refused: mail is filed there without anyone asking, and
            renaming one breaks that in a way no tool call can explain. Tell the
            user to do that in their mail client if they really mean it.
            """
            provider, sub = await self._get_provider(account, writes=True)
            rename = getattr(provider, "rename_folder", None)
            if rename is None:
                raise ValidationError(
                    "This mail account's backend cannot rename folders. Do it in "
                    "a mail client."
                )
            if not (name or "").strip() or not (new_name or "").strip():
                raise ValidationError("'name' and 'new_name' are both required")
            renamed = await run_blocking(provider, rename, name, new_name)
            self._track_usage(sub, "mail_rename_folder")
            return FolderRenameResult(folder=renamed, previous_name=name)

        @self.app.tool(
            title="Delete Folder",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def mail_delete_folder(
            name: str,
            confirm: bool = False,
            account: Optional[str] = None,
        ) -> FolderDeleteResult:
            """Delete an EMPTY folder. Requires confirm=true.

            Unlike mail_delete, which files messages in the Trash, this cannot
            be undone -- so it only ever deletes a folder that holds no mail and
            no sub-folders, and refuses with the count when it does. That is not
            an obstacle to work around: empty it the recoverable way first, with
            mail_delete (messages go to Trash) or mail_move, and then delete the
            folder. NEVER present "delete the folder with everything in it" as
            something you can do.

            Args:
                name: The folder's full name, exactly as mail_list_folders
                    reports it.
                account: Which email account (id or address from
                    mail_list_accounts). Omit when only one is configured.

            INBOX and the folders with a ``role`` are refused, as in
            mail_rename_folder.
            """
            provider, sub = await self._get_provider(account, writes=True)
            delete_folder = getattr(provider, "delete_folder", None)
            if delete_folder is None:
                raise ValidationError(
                    "This mail account's backend cannot delete folders. Do it in "
                    "a mail client."
                )
            if not (name or "").strip():
                raise ValidationError("'name' is required (the folder to delete)")
            require_confirm(confirm, "Deleting a folder")
            gone = await run_blocking(provider, delete_folder, name)
            self._track_usage(sub, "mail_delete_folder")
            return FolderDeleteResult(folder=gone)
