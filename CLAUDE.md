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
- `providers/factory.py` -- picks the provider from `SQUIRREL_MAIL_PROVIDER`.
- `SoverinImapClient.flag` is the one deliberate exception to principle 3: it
  issues its own `UID STORE` instead of calling `imap-tools`' `mb.flag`, because
  that helper follows every STORE with an `EXPUNGE` -- which would permanently
  drop whatever another mail client left marked `\Deleted` in the folder.
  Flagging is advertised as non-destructive, so it does not get to delete
  anything. `tests/test_imap_flag.py` pins the command shape and the absent
  expunge; the GreenMail e2e proves both against a real server.
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
| `usage.py` | Usage-tracking stub (full version in admin package) |
