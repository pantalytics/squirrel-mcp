"""Server instructions handed to the MCP client at handshake.

Teaches the client how to use Squirrel's mail tools well: paginate, chunk large
bodies, and -- critically -- confirm with the user before anything is sent or moved.
"""

SERVER_INSTRUCTIONS = """\
Squirrel exposes the user's OWN mailbox, contacts and calendar over IMAP/SMTP,
CardDAV and CalDAV. It runs locally; data never leaves the user's machine. Three
tool families: `mail_*`, `contacts_*`, `calendar_*` (a family is only present when
that pillar is configured).

Mail tools (all prefixed `mail_`):
- mail_list_accounts: list the configured email accounts. Every mail tool takes
  an optional `account` argument (id or address from here); omit it with a
  single account, pass it explicitly when there are several -- and always tell
  the user which address a message will be sent from.
- mail_list_folders: list mailboxes/folders. Start here to learn folder names.
- mail_search: search one folder, newest first. Paginate with limit/offset; the
  mailbox can be large, so never try to pull everything at once. `unseen_only`
  and `flagged_only` narrow it server-side -- use `flagged_only=true` to answer
  "what have I flagged", rather than paging the folder and sifting yourself.
- mail_read: read one message by uid. Large bodies are truncated -- the response
  tells you the total length and how to page the rest.
- mail_read_chunk: fetch the next slice of a large body (offset + length).
- mail_get_attachment: download one attachment by its index (from mail_read).
- mail_draft / mail_edit_draft: create or update a draft in the Drafts folder.
- mail_send: send a message. DESTRUCTIVE/OUTGOING. It requires confirm=true and
  will refuse otherwise. ALWAYS show the user the exact recipients, subject,
  body and the account it will be sent from, and get explicit approval BEFORE
  calling it with confirm=true. The result's `from_address` is the address the
  message actually went out from -- repeat it back to the user.
- ATTACHMENTS go out via the `attachments` argument on mail_send, mail_draft and
  mail_edit_draft. Two forms, and the choice matters: to send on a file that is
  already in the mailbox -- forwarding an invoice, passing on a contract -- use
  `{"source_uid": "<uid>", "source_index": <n>, "source_folder": "INBOX"}` with
  the index from mail_read, and the file never passes through you at all. Only
  use `{"filename": "...", "content_base64": "..."}` for bytes that exist
  nowhere else, and keep them small: base64 is a third larger than the file and
  every byte of it costs you context. NEVER call mail_get_attachment and paste
  the result back as content_base64 -- that is the same file twice through you
  when source_uid would have moved it for nothing. Name every attachment when
  you ask the user to approve a send: a file leaving the mailbox is as much a
  decision as the recipient is. Editing a draft leaves its existing attachments
  alone unless you pass the argument.
- REPLYING is not the same as sending: whenever the user is answering a message
  they just read, pass that message's uid as `reply_to_uid` (plus the
  `reply_to_folder` it lives in) to mail_send or mail_draft. Only that puts the
  message inside the existing thread; a subject starting with "Re:" does not --
  Gmail may guess it back into the conversation, Outlook generally will not,
  and the user ends up with a second thread they did not ask for. With
  `reply_to_uid` you may omit `to` and `subject` (taken from the original) and
  pass `reply_all=true` to keep the other participants on cc. The result's
  `in_reply_to` names the message that was answered -- say so when reporting
  back. If you are unsure the user meant a reply, ask before sending, not after.
- mail_move: move messages between folders. Requires confirm=true. Confirm with
  the user first, and double-check the destination folder name via mail_list_folders.
- mail_flag: set or clear the \\Flagged marker -- the star every mail client
  draws -- on messages in a folder. `flagged=false` takes it back off. No
  confirmation needed, because it changes no message and is reversible by the
  same tool, so it is the natural way to mark things for the user to follow up
  on. Flagged messages come back from mail_search with "\\Flagged" in `flags`.

Contacts tools (`contacts_*`, CardDAV): contacts_list_addressbooks · contacts_search
(paginated) · contacts_read · contacts_create / contacts_update / contacts_delete
(all writes need confirm=true). Resolve the addressbook id via
contacts_list_addressbooks first.

Calendar tools (`calendar_*`, CalDAV): calendar_list_calendars · calendar_search_events
(ISO date window, defaults to ~±6 months) · calendar_read_event · calendar_create_event
/ calendar_update_event / calendar_delete_event (all writes need confirm=true).
Dates/times are ISO 8601 (YYYY-MM-DD for all-day, otherwise full datetime).

Good habits:
- Resolve folder/addressbook/calendar ids with the matching list_* tool first.
- Prefer drafting (mail_draft) and letting the user review over sending directly.
- ALWAYS show the user exactly what will change and get approval before calling
  any write tool (send/move/create/update/delete) with confirm=true.
- uids are per-collection identifiers returned by the search/read tools; pass the
  same folder/addressbook/calendar you found them in.
"""
