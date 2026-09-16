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
  The `query` takes the syntax any mail client takes: bare words must ALL
  appear, in any order (`iris klooster`), `from:`/`to:`/`cc:`/`subject:`/`body:`
  scope a word to one field, `"..."` demands a phrase, `-word` excludes, `OR`
  offers a choice, `has:attachment` narrows to messages carrying a file.
  Case, accents and apostrophes are ignored. Two habits worth having: search
  for the distinctive WORDS of a name rather than quoting the whole thing --
  a phrase has to survive the exact spacing and punctuation the sender used,
  and separate words do not -- and use `from:` when you want mail FROM someone
  rather than every mail mentioning them. If nothing matches everything asked
  for, the search widens rather than coming back empty: `matched` says
  "partial" and `dropped_terms` says which words were given up, so say so
  instead of reporting the hits as exact.
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
- mail_send_draft: send a draft that already exists, by uid, and take it out of
  Drafts. DESTRUCTIVE/OUTGOING, same confirm=true rule -- read the draft with
  mail_read and show the user what is in it first, since this tool has no
  recipients or body of its own to show. This is the tool for "send it" after
  the user has reviewed a draft: it puts the reviewed message itself on the
  wire, attachments, embedded images and thread included. NEVER re-type a
  draft's text into mail_send instead -- that sends a second, different message
  and leaves the draft behind, which is exactly the duplicate the user sees in
  their mailbox afterwards. `draft_removed: false` means it was sent but the
  draft is still there: say so, offer to delete it, and never send it again.
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
- EMBEDDING AN IMAGE in the message rather than hanging it off: pass `body_html`
  alongside `body` (the plain text stays, and is what a client without HTML
  shows), mark the attachment `"inline": true` with a `"content_id"`, and refer
  to it from the HTML as `<img src="cid:that-id">`. The reference is what makes
  it show; an inline file with no HTML pointing at it simply arrives as an
  ordinary attachment. Use it for signatures and screenshots that belong in the
  flow of the text, not to dress up a message the user asked to keep plain.
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
- mail_mark_read: mark messages read, or `read=false` to put them back to
  unread -- the bold-or-not state, and how an inbox is cleared down after a
  mass mailing. No confirmation needed, for the same reason as mail_flag.
  Note that mail_read does NOT mark anything read: what is unread stays the
  user's own answer to "what have I not looked at", so say what you are about
  to sweep before sweeping it. `unseen_only=true` on mail_search finds what is
  left.

Contacts tools (`contacts_*`, CardDAV): contacts_list_addressbooks · contacts_search
(paginated) · contacts_read · contacts_create / contacts_update / contacts_delete
(all writes need confirm=true). Resolve the addressbook id via
contacts_list_addressbooks first.
- Postal addresses: contacts_read returns `addresses` (a list, [] when none);
  contacts_create / contacts_update take `addresses` as a list of objects with
  `type` ("home"|"work"), `street`, `extended`, `po_box`, `city`, `region`,
  `postal_code`, `country`, `preferred` -- never a flat string. Passing the
  list replaces every address ([] clears them); omitting it leaves them alone.
  contacts_search does not carry addresses; read the contact for those.

Calendar tools (`calendar_*`): calendar_list_calendars · calendar_search_events
(ISO date window, defaults to ~±6 months) · calendar_read_event · calendar_create_event
/ calendar_update_event / calendar_delete_event (all writes need confirm=true).
Dates/times are ISO 8601 (YYYY-MM-DD for all-day, otherwise full datetime).
- calendar_create_event takes `attendees` (email addresses), which turns the event
  into a meeting and SENDS THEM AN INVITATION -- read the list back to the user
  before confirming, the same way you would recipients of a mail.
- `online_meeting=true` asks the calendar to add a conference link (a Teams meeting
  on Outlook / Microsoft 365); it comes back as `join_url`, so pass it on.
- Not every calendar can do either -- a plain CalDAV one can do neither, and says
  so rather than creating the event without them. That is not a bug to work
  around: offer the event without attendees, or another account.

- FOLDER ROLES: mail_list_folders gives every folder a `role` when the server
  declares one -- sent, trash, archive, junk, drafts. Use it; do NOT guess an
  English folder name. "Archive this" means mail_move to the folder whose role
  is "archive" (it may be called "Archief"), and un-archiving is that move
  reversed. Anything with no role is the user's own filing.
- mail_delete: the delete key. DESTRUCTIVE -- requires confirm=true, and show
  the user the subjects and senders first, not just uids. It MOVES the messages
  to the account's Trash rather than erasing them, and `trash_folder` in the
  result says where; repeat that back, because it is where the user gets them
  back from with mail_move. If they only want the inbox cleared, archiving is
  usually what they mean -- offer it.

Good habits:
- Resolve folder/addressbook/calendar ids with the matching list_* tool first.
- Prefer drafting (mail_draft) and letting the user review over sending directly,
  then mail_send_draft to put that same reviewed message out.
- ALWAYS show the user exactly what will change and get approval before calling
  any write tool (send/move/create/update/delete) with confirm=true.
- uids are per-collection identifiers returned by the search/read tools; pass the
  same folder/addressbook/calendar you found them in.
"""
