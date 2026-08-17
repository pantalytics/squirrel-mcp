# CLAUDE.md -- Instructions for Claude Code

## What this project is

**Squirrel** (`squirrel-mcp`) -- a local, sovereign **personal-information hub**
exposed as an MCP server. It hamsters *your own* data and hands it to Claude and
other MCP clients, without that data ever leaving your machine.

Squirrel is the face; the providers underneath are **swappable backends**.
v1 ships one backend: generic **IMAP/SMTP over TLS** (`SQUIRREL_MAIL_PROVIDER=imap`),
which works with any mailbox once the user supplies the host names. There are
deliberately **no per-provider presets** -- users look their own hosts up. Later:
Gmail, Outlook (API-based, so they get their own provider).

This is the **public** package. The hosted, multi-tenant SaaS (admin panel,
Zitadel login, PostgreSQL, Stripe billing, PostHog, Hetzner deploy) lives in the
private repo `pantalytics/squirrel-mcp-admin`, which imports this package as a
tag-pinned dependency and adds those features on top. Same open-core split as
`odoo-mcp-pro` / `odoo-mcp-pro-admin` -- Squirrel is its little brother and
reuses its patterns deliberately.

## Three pillars (end state)

`mail.*`, `contacts.*`, `calendar.*`. v1 builds **only mail**. The other two
namespaces are reserved so they plug in later as sibling providers + tool mixins
**without rewriting** what exists.

## Design principles

1. **Own your data / sovereignty** -- runs locally, talks straight to your
   provider over TLS, no mail content to third parties.
2. **Swappable backends** -- tools are provider-agnostic; they only ever touch
   the `MailProvider` protocol, never a concrete IMAP/API client.
3. **Don't reinvent the wheel** -- lean on mature libraries (`imap-tools` for
   IMAP, stdlib `smtplib`/`email` for SMTP) instead of hand-rolling protocol code.
4. **Confirm before it leaves or changes** -- sending and moving/deleting require
   an explicit `confirm=true` and are flagged `destructiveHint`. The line is
   "could the user not get this back", not "is this a write": `mail_flag` sets
   and clears the same marker with the same tool and alters no message, so
   gating it would only teach clients that the confirm prompt is noise.
5. **No fallbacks** -- explicit config or a clear error, never guess credentials.
6. **Open core** -- public package works standalone (stdio, one mailbox from env);
   the private package adds SaaS features via well-defined seams.

## Key architecture facts

- `providers/protocol.py` -- `MailProvider` (typing.Protocol). Every backend
  satisfies it. `ContactsProvider` / `CalendarProvider` are reserved here.
- `providers/soverin/` -- `SoverinMailProvider` (historical name; it is the plain
  IMAP/SMTP backend) delegates to `imap-tools` (IMAP) and stdlib SMTP. This is the
  only file that knows about IMAP/SMTP.
- `providers/factory.py` -- picks the provider from `SQUIRREL_MAIL_PROVIDER` via
  `MAIL_PROVIDER_REGISTRY` (one lazily-loaded entry per backend, the same shape
  as pan_mail_pro's `PROVIDER_CLIENTS`). `tests/test_provider_contract.py`
  guards the seam: registry and config name the same backends, every registered
  backend implements the whole `MailProvider` protocol (a structural Protocol
  fails at first call, not at import -- the test moves that to `make test`),
  and the tool layer contains no reference to a concrete client, which used to
  be only a convention below.
- `SoverinImapClient.flag` is the one deliberate exception to principle 3: it
  issues its own `UID STORE` instead of calling `imap-tools`' `mb.flag`, because
  that helper follows every STORE with an `EXPUNGE` -- which would permanently
  drop whatever another mail client left marked `\Deleted` in the folder.
  Flagging is advertised as non-destructive, so it does not get to delete
  anything. `tests/test_imap_flag.py` pins the command shape and the absent
  expunge; the GreenMail e2e proves both against a real server.
- **A search query is parsed once, here, not interpreted by each backend.**
  `search_query.py` owns the grammar (`from:`/`to:`/`cc:`/`subject:`/`body:`,
  `"phrases"`, `-exclusions`, `OR`, `has:attachment`) and both backends compile
  the same `MailQuery` -- IMAP into its search keys, the admin package's Graph
  provider into KQL. It replaced handing the raw string to whoever was
  answering, which failed twice over. **A literal is the wrong default**: IMAP's
  `TEXT` is a substring of the raw message, so a contact's name missed a
  signature reading `Iris  van 't Klooster` over the second space -- and would
  equally have missed a header fold, a curly apostrophe or `Klooster, Iris`.
  Unquoted words now AND as separate keys, which none of those defeat, and a
  phrase is what you get by quoting. **And the fallback was undefined**: RFC
  3501 explicitly lets a server "implement flexible matching" for `TEXT`, so
  one host word-matched and ignored order while the next did strict substring
  and Graph ran keywords -- the same tool meaning three things, which is the
  divergence the attachment and meeting seams are held against.
  Three rules make the backends agree. The compiled query is always **at least
  as broad** as what was asked (`José` leaves as `Jos`, since no server will
  fold accents for us and sending one spelling misses the other). Anything a
  `MessageSummary` can prove -- sender, recipients, subject, attachment flag --
  is then **verified exactly** here, taking that breadth back; anything it
  cannot (the body, cc) stays the server's word, because a 200-character
  preview refutes nothing. And when a query matches nothing the tool **widens
  and says so**: `widen()` gives up one term at a time, least distinctive
  first, and `matched`/`dropped_terms` carry which. That ladder tries *each*
  term in turn rather than guessing once, because the absent word is usually
  the rarest one, so a single "drop the weakest" keeps the word that is missing
  and stays empty. ORing the terms together is deliberately not on it: it
  always "works" and never informs. `parsed` is an **additive** keyword on
  `MailProvider.search` offered via signature inspection, so a backend written
  before it keeps its raw `query`. `tests/test_search_query.py` pins the
  grammar and the ladder, `tests/test_mail_search.py` the IMAP keys and the
  widening policy, and the GreenMail e2e proves a real server answers them.
- **Replying is a provider concern, not a header the tool layer writes.**
  `mail_send` / `mail_draft` take a `reply_to_uid` (+ `reply_to_folder`) and
  hand it to the provider untouched, because *how* you join a thread is the
  transport's business: IMAP/SMTP reads the parent's `Message-ID` /
  `References` and sends the matching headers (`imap.reply_headers` →
  `mime.build_email`, joined in `provider.py`, which is the only thing that
  sees both halves), while an API backend may have a conversation of its own to
  join -- the admin package's Graph provider uses `createReply` so Outlook's
  `conversationId` is Outlook's to assign. What the *tool* layer derives is
  only what a human would see: the recipient (`Reply-To`, else `From`), the
  `Re:` subject, and reply-all's cc list. Two things that look incidental and
  are not: `update_draft` carries the threading of the draft it replaces (an
  edit rewrites the message, so a reviewed reply would otherwise turn back into
  a new conversation at the moment it is sent), and `mime.reply_chain` trims a
  long `References` to `MAX_REFERENCES` keeping the thread root plus the
  nearest ancestors. `tests/test_reply_threading.py` and
  `tests/test_imap_threading.py` pin the seams; the GreenMail e2e reads the
  delivered headers back off a real server.
- **Sending is delivery; the copy in Sent is a second job, and nobody else
  does it.** SMTP hands a message to the recipient's server and keeps nothing,
  and most IMAP hosts (Soverin included) file no copy of their own -- so a mail
  sent through Squirrel used to be genuinely gone: delivered, and absent from
  the sender's own Sent folder, which is where `mail_search` and every mail
  client look. A send you cannot find afterwards reads as a send that never
  happened. So `provider.send` now finishes the job: `smtp.send` hands back the
  **exact RFC822 bytes** it delivered plus the moment it did, and `provider.py`
  -- the one place that sees both halves, exactly as with threading -- APPENDs
  them to the Sent folder `\Seen`, with the send time as INTERNALDATE so the
  copy sorts where it belongs. Rebuilding the message instead would mint a
  fresh Message-ID and Date, and the copy would no longer be the mail the
  recipient got. Four decisions hold it up. **The folder is read, not
  guessed**: the `\Sent` SPECIAL-USE attribute (RFC 6154) off the same LIST
  `mail_list_folders` already returns, because the name is localized
  ("Verzonden items"), sometimes under INBOX, and "Sent Items"/"Sent Messages"
  are both common -- `Sent` is only the fallback when a server advertises
  nothing. **A failed APPEND does not fail the send**: the message is with the
  recipient by then, so raising would report a delivered mail as undelivered
  and invite a retry that sends it twice -- the outcome rides back in
  `saved_to_sent` / `sent_folder` on `SendResult` instead, and the tool tells
  the user. **The Bcc header stays on the copy** and only comes off what
  leaves: your own Sent folder is the only record of whom you blind-copied.
  **And a host that files its own copy gets one, not two** -- the Message-ID is
  searched for in the folder before the APPEND, which is also why the Graph
  backend simply reports `saved_to_sent=True`: `/send` files Sent Items
  server-side and an API backend has no APPEND to duplicate it with.
  `tests/test_sent_copy.py` pins the discovery, the flags, the deduplication
  and the never-fatal rule; the GreenMail e2e sends for real and reads the copy
  back out of Sent.
- **A meeting is not an appointment, and CalDAV only does the second.**
  `calendar_create_event` takes `attendees` and `online_meeting`, and both are
  gated on a capability the provider declares -- `supports_attendees` /
  `supports_online_meeting`, read as `getattr(..., False)` so a backend written
  before them answers no. This is the attachments rule (below) applied to the
  calendar pillar, and it is the *reason* the flags exist rather than a
  best-effort write: putting an `ATTENDEE` line in an iCalendar object does not
  invite anybody. Delivering the invitation is server-side scheduling
  (RFC 6638), which some CalDAV servers implement and others quietly do not,
  and no client can tell which it is talking to -- so the CalDAV backend
  answers False to both and names the part that will work instead. Microsoft
  Graph, in the admin package, answers True: `isOnlineMeeting` +
  `onlineMeetingProvider` mint a real Teams meeting, and Graph mails the
  invitations itself. The link comes back as `join_url` on `EventDetail`, and
  `calendar_create_event` re-reads the event to report it -- a meeting nobody
  can join is half an answer, and a read-back that fails is logged rather than
  raised, because the event *was* created and a failed tool call invites a
  retry that books it twice. `attendees` accepts the shapes mail recipients
  accept (`as_str_list`). `tests/test_calendar_invitations.py` pins the
  refusals and the capable path.
- **Sending an attachment is a MIME concern, not a protocol feature.** Neither
  SMTP nor IMAP knows what an attachment is -- both carry one opaque RFC 5322
  blob -- so the whole outgoing mechanism is `mime.build_email` handing files to
  the stdlib's `add_attachment` (which promotes the message to
  `multipart/mixed`, picks base64 and RFC 2231-encodes a non-ASCII filename).
  One function, and both the SMTP and the IMAP-draft path get it, because both
  already built their message there. Four decisions around it are load-bearing:
  **How the bytes get in.** `tools/mail/attachments.py` takes either
  `{"source_uid", "source_index"}` -- the provider fetches the part and hands it
  straight back, so forwarding never routes a file through the model's context
  -- or `{"filename", "content_base64"}` for bytes that exist nowhere else.
  There is deliberately **no file path**: the hosted multi-tenant server shares
  this exact tool layer, where a path is an arbitrary read of the *server's*
  disk, and one argument meaning two things per deployment is the divergence
  everything else here is held against.
  **Refusing beats dropping.** A provider advertises
  `supports_outgoing_attachments`, read as `getattr(..., False)`. A backend that
  merely ignored the kwarg would send the mail *without* the file and report
  success -- so the Graph provider in the admin package, which cannot do this
  yet, gets a clear refusal rather than silent data loss.
  **The limit comes from the server.** `smtp._check_size` reads SMTP's SIZE
  extension (RFC 1870) out of the EHLO reply and refuses before DATA -- Soverin
  answers 70 MiB, Gmail 35 -- so there is no per-provider number to keep
  current. `MAX_OUTGOING_TOTAL_BYTES` is a separate, smaller ceiling: what the
  *receiving* world accepts. And the socket timeout now scales with the payload,
  because a flat 10s silently demanded a 20 Mbit/s uplink to send 25 MB.
  **An edit keeps what it does not mention.** Exactly like the threading above:
  `update_draft` re-reads the old revision's attachments when `attachments` is
  None, because fixing a typo in a covering note must not drop the file the note
  is about. `[]` strips them on purpose.
  `tests/test_attachments_outgoing.py` pins the seams; the GreenMail
  e2e proves a real server takes it and hands the same bytes back.
- **Inline is the HTML compose path that unblocked it.** Inline was deferred
  above for a good reason -- it means nothing without an HTML body referencing
  the part -- so `body_html` came first and the reason is now *enforced* rather
  than avoided. `body_html` is added as an **alternative**: `body` stays the
  text a plain-text client shows, and both halves are the same message. An
  attachment marked `inline` (with a `content_id` the tool layer fills in when
  the caller omits one, since a part nothing can name is a part nothing can
  show) is only *placed* inline when there is HTML to point at it, and
  degrades to an ordinary attachment when there is not — keeping its
  `Content-ID` for a client that wants it anyway. That placement is the whole
  feature and lives in `mime._add_parts`: an inline part goes inside
  `multipart/related` next to the HTML, because a client resolves `cid:` within
  that group and nowhere else, while the same image parked in the outer
  `multipart/mixed` beside the paperclips renders in some clients and arrives
  as a second paperclip in the rest. So `mixed[alternative[text,
  related[html, image]], file]` is the shape, and a message with neither HTML
  nor attachments is still the one `text/plain` part it always was.
  `content_id` is normalised from the three spellings a model writes (`logo`,
  `cid:logo`, `<logo>`). `tests/test_attachments_inline.py` pins the trees and
  the id handling; the GreenMail e2e reads the delivered `multipart/related`
  back off a real server.
- `providers/soverin/contacts.py` **discovers** the address-book home rather than
  assuming a path. CardDAV standardises none, so `carddav_url` is a starting
  point: RFC 6764's `current-user-principal` → `addressbook-home-set` hops turn a
  bare host name (iCloud, GMX, Yandex), a discovery root (mailbox.org) and the
  home collection itself (Fastmail) into the same books. It used to append
  `/addressbooks/` to whatever it was given -- Soverin's layout, and nobody
  else's -- which is what kept a *prefilled* CardDAV URL out of reach: only a
  home collection spelled out per customer would have worked. That path survives
  as the fallback for a server answering neither property, and
  `tests/test_carddav_discovery.py` pins all of it, including that a 401 stays a
  401 instead of being retried into "no address books found". The CalDAV side
  needs none of this: the `caldav` library's `principal()` already does it.
- Blocking IMAP/SMTP calls run off the event loop via `tools/_common.run_blocking`
  (per-provider `asyncio.Lock` -> one socket is never used by two threads).
- Single-tenant: one mailbox from env vars (stdio or HTTP). The hosted
  multi-tenant deployment lives in the private admin package.

## Open-core extension contract

The admin package subclasses/imports these -- rename only in coordination with it:

- `server.create_fastmcp_app(*, auth=None, token_verifier=None, extra_instructions=None)`
  -- single source of truth for FastMCP construction; admin's multi-tenant entry
  point uses it.
- `tools.handler.MailToolHandler._get_provider` -- hook the admin overrides to
  resolve a per-user provider from the authenticated subject.
- `tools.handler.MailToolHandler._track_usage` -- usage-tracking hook (no-op here).
- `tools._common._current_sub` -- contextvar carrying the authenticated subject.
- `usage.track_event` -- no-op stub here; the real tracker lives in admin.

## Development & testing

```bash
make install                    # uv venv + dev deps
make lint                       # ruff + ty
make test                       # unit tests (mocked), no docker
make test-int                   # real IMAP/SMTP e2e against GreenMail (docker)
make docker-smoke               # build image + full MCP handshake over http
make playwright                 # browser e2e (web/console.html) vs the stack
make test-all                   # all of the above, then tear down
```

Test layers:
- **Unit** (`tests/`, marker: none) -- mocked `FakeMailProvider`, no network.
- **Integration** (`tests/integration/`, marker `integration`) -- a real
  **GreenMail** IMAP/SMTP server in Docker (`docker-compose.test.yml`), exercising
  the actual provider. No secrets; login is `squirrel` / email `squirrel@example.com`.
- **Docker smoke** (`scripts/mcp_smoke.py`) -- MCP handshake + `mail_list_folders`
  against Squirrel-in-a-container wired to GreenMail.
- **Playwright** (`e2e/`) -- `web/console.html` drives the containerised server in a
  real browser. Needs `SQUIRREL_DEV_CORS=true` on the server (set in the test compose).

For a manual check against your own mailbox, fill in `.env` and run
`python scripts/e2e_soverin.py` (hits the live server, read-only).

Transport-security knobs (`config.imap_security`/`smtp_security` = ssl|starttls|plain,
`tls_verify`) default to secure SSL; the e2e stack sets them to `plain` for the
local GreenMail server. `mail_username` lets the login differ from the email
address (GreenMail logs in with the local part). `imap_host`/`smtp_host` have no
defaults -- a missing one is a config error, never a guess.

## Conventions

- Follow existing style (ruff configured in `pyproject.toml`).
- Tools never import a concrete client -- only `providers.protocol`.
- Every backend must satisfy `MailProvider`.
- Secrets only via env / `.env` (gitignored). No hardcoded fallbacks.
- Keep the mail engine's provider code out of the tool layer.
- Max ~500 lines per Python file; split into mixins like `tools/mail/*`.

## Key files

| File | Role |
|------|------|
| `server.py` | `create_fastmcp_app()` factory, FastMCP setup, stdio/HTTP runners |
| `__main__.py` | CLI entry: argparse, transport selection |
| `config.py` | `SquirrelConfig` dataclass + env loading + provider selection |
| `providers/protocol.py` | `MailProvider` protocol (+ reserved contacts/calendar) |
| `providers/soverin/` | IMAP/SMTP backend: provider, imap (imap-tools), smtp (stdlib) |
| `providers/factory.py` | Provider selection from config |
| `tools/handler.py` | `MailToolHandler` (mixins) + `register_tools` |
| `tools/mail/` | Mail tools as mixins: folders, query, read, compose, organize |
| `tools/_common.py` | `run_blocking`, logger, limits, `_current_sub` |
| `schemas.py` | Pydantic result models |
| `knowledge.py` | Server instructions handed to the MCP client |
| `error_handling.py` / `error_sanitizer.py` | Error hierarchy + message sanitizing |
| `logging_config.py` | Structured logging to stderr |
| `search_query.py` | Search grammar: parse, fold, verify, widen (both backends) |
| `usage.py` | Usage-tracking stub (full version in admin package) |
