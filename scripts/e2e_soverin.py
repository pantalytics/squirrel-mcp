#!/usr/bin/env python3
"""Read-only end-to-end check against your live Soverin mailbox.

Reads credentials from .env (or the environment), logs in over IMAP, lists your
folders, shows the newest few INBOX subjects, and reads the latest message.
Nothing is sent, moved, or deleted.

    python scripts/e2e_soverin.py
"""

from __future__ import annotations

import sys

from squirrel_mcp.config import load_config
from squirrel_mcp.providers import create_mail_provider


def main() -> int:
    try:
        config = load_config()
    except ValueError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    provider = create_mail_provider(config)
    print(f"Connecting to {config.imap_host}:{config.imap_port} as {config.mail_email} ...")
    provider.connect()
    print("  logged in\n")

    try:
        folders = provider.list_folders()
        print(f"Folders ({len(folders)}):")
        for f in folders:
            print(f"  - {f.name}")
        print()

        messages, total = provider.search("INBOX", limit=5, offset=0)
        print(f"INBOX: {total} message(s). Newest {len(messages)}:")
        for m in messages:
            flag = " " if "\\Seen" in m.flags else "*"
            att = " [attach]" if m.has_attachments else ""
            print(f"  {flag} uid={m.uid}  {m.date}  {m.from_addr}{att}")
            print(f"      {m.subject}")
        print()

        if messages:
            newest = messages[0]
            detail = provider.fetch_message("INBOX", newest.uid)
            print(f"Reading uid={detail.uid}: {detail.subject}")
            print(f"  from: {detail.from_addr}")
            print(f"  body length: {detail.body_length} chars")
            print(f"  attachments: {[a.filename for a in detail.attachments]}")
            print("  --- preview ---")
            print("  " + detail.body_text[:300].replace("\n", "\n  "))
    finally:
        provider.disconnect()

    print("\nOK -- Squirrel can talk to your Soverin mailbox.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
