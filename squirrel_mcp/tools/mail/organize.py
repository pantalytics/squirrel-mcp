"""mail_move -- move messages between folders (requires confirmation).
mail_flag -- set or clear the \\Flagged marker (no confirmation needed).
mail_mark_read -- set or clear the \\Seen marker (no confirmation needed).
"""

from __future__ import annotations

from typing import Any, Optional

from mcp.types import ToolAnnotations

from ...error_handling import ValidationError
from ...schemas import FlagResult, MoveResult, SeenResult
from .._common import as_str_list, require_confirm, run_blocking


class OrganizeToolsMixin:
    """Move messages between folders, flag them, and mark them read or unread."""

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

        @self.app.tool(
            title="Flag Mail",
            annotations=ToolAnnotations(
                # A write, but the one write with nothing to lose: the flag is
                # metadata, setting it twice changes nothing, and the same tool
                # takes it straight back off. Hence no confirm= gate, unlike
                # mail_move and mail_send.
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def mail_flag(
            uids: Any,
            folder: str = "INBOX",
            flagged: bool = True,
            account: Optional[str] = None,
        ) -> FlagResult:
            """Flag or unflag one or more messages -- the star most mail clients show.

            Args:
                uids: Message uids from mail_search in ``folder``. Accepts a list
                    or a comma-separated string.
                folder: Folder the messages live in (default "INBOX"). Must be the
                    folder the uids came from -- a uid is only meaningful there.
                flagged: True to set the flag, false to remove it again.
                account: Which email account (id or address from
                    mail_list_accounts). Omit when only one is configured.

            The flag survives in the mailbox and is visible in every other mail
            client, so it is a real way to mark messages for the user to follow up
            on. Flagged messages come back from mail_search with "\\Flagged" in
            their ``flags``.
            """
            provider, sub = await self._get_provider(account, writes=True)
            uid_list = as_str_list(uids)
            if not uid_list:
                raise ValidationError("'uids' is required (at least one message uid)")
            changed = await run_blocking(provider, provider.flag, folder, uid_list, flagged)
            self._track_usage(sub, "mail_flag")
            return FlagResult(
                changed=changed,
                flagged=flagged,
                folder=folder,
                uids=uid_list,
            )

        @self.app.tool(
            title="Mark Mail Read",
            annotations=ToolAnnotations(
                # Same shape as mail_flag, and for the same reason: \Seen is a
                # marker, not the message, and this tool takes it straight back
                # off again. Nothing to gate.
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
        )
        async def mail_mark_read(
            uids: Any,
            folder: str = "INBOX",
            read: bool = True,
            account: Optional[str] = None,
        ) -> SeenResult:
            """Mark one or more messages read, or put them back to unread.

            Args:
                uids: Message uids from mail_search in ``folder``. Accepts a list
                    or a comma-separated string.
                folder: Folder the messages live in (default "INBOX"). Must be the
                    folder the uids came from -- a uid is only meaningful there.
                read: True to mark them read, false to mark them unread again.
                account: Which email account (id or address from
                    mail_list_accounts). Omit when only one is configured.

            This is the bold-or-not state every mail client shows, and it is how
            an inbox is cleared down after a mass mailing. Reading a message with
            mail_read does *not* set it -- what is unread stays the user's own
            answer to "what have I not looked at yet" -- so marking is always
            deliberate. Messages marked read come back from mail_search with
            "\\Seen" in their ``flags``, and ``unseen_only=true`` finds the rest.
            """
            provider, sub = await self._get_provider(account, writes=True)
            uid_list = as_str_list(uids)
            if not uid_list:
                raise ValidationError("'uids' is required (at least one message uid)")
            # Refusing beats dropping: a backend from before this existed would
            # otherwise report a clean sweep it never made.
            set_seen = getattr(provider, "set_seen", None)
            if set_seen is None:
                raise ValidationError(
                    "This mail account cannot change read/unread state. "
                    "mail_flag works on it, and marks messages just as visibly."
                )
            changed = await run_blocking(provider, set_seen, folder, uid_list, read)
            self._track_usage(sub, "mail_mark_read")
            return SeenResult(
                changed=changed,
                read=read,
                folder=folder,
                uids=uid_list,
            )
