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
- mail_list_folders: list mailboxes/folders. Start here to learn folder names.
- mail_search: search one folder, newest first. Paginate with limit/offset; the
  mailbox can be large, so never try to pull everything at once.
- mail_read: read one message by uid. Large bodies are truncated -- the response
  tells you the total length and how to page the rest.
- mail_read_chunk: fetch the next slice of a large body (offset + length).
- mail_get_attachment: download one attachment by its index (from mail_read).
- mail_draft / mail_edit_draft: create or update a draft in the Drafts folder.
- mail_send: send a message. DESTRUCTIVE/OUTGOING. It requires confirm=true and
  will refuse otherwise. ALWAYS show the user the exact recipients, subject and
  body and get explicit approval BEFORE calling it with confirm=true.
- mail_move: move messages between folders. Requires confirm=true. Confirm with
  the user first, and double-check the destination folder name via mail_list_folders.

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
