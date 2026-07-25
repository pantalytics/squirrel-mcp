#!/usr/bin/env python3
"""Smoke-test a running Squirrel MCP server over streamable-http.

Performs the full MCP handshake, lists tools, and calls mail_list_folders. Used
by the Docker test (against the container wired to GreenMail) and handy for any
running server. Exits non-zero on the first failure.

    python scripts/mcp_smoke.py [http://localhost:8000/mcp]
"""

from __future__ import annotations

import json
import sys
import urllib.request

EXPECTED_TOOLS = {
    "mail_list_folders",
    "mail_search",
    "mail_read",
    "mail_read_chunk",
    "mail_get_attachment",
    "mail_draft",
    "mail_edit_draft",
    "mail_send",
    "mail_move",
}


def _post(url: str, body: dict, sid: str | None = None):
    data = json.dumps(body).encode()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if sid:
        headers["mcp-session-id"] = sid
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    resp = urllib.request.urlopen(req, timeout=15)
    raw = resp.read().decode()
    # json_response mode returns plain JSON; tolerate an SSE-framed reply too.
    if "data:" in raw[:16]:
        raw = next((ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")), "")
    parsed = json.loads(raw) if raw.strip() else None
    return resp.status, resp.headers.get("mcp-session-id"), parsed


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else "http://localhost:8000/mcp"
    print(f"Smoke-testing {url}")

    status, sid, body = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "squirrel-smoke", "version": "0"},
            },
        },
    )
    assert status == 200, f"initialize returned {status}"
    server = body["result"]["serverInfo"]
    print(f"  initialize OK -- server {server['name']} {server['version']}")

    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)

    _, _, tl = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid)
    names = {t["name"] for t in tl["result"]["tools"]}
    missing = EXPECTED_TOOLS - names
    assert not missing, f"missing tools: {missing}"
    print(f"  tools/list OK -- {len(names)} tools")

    _, _, cr = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "mail_list_folders", "arguments": {}},
        },
        sid,
    )
    result = cr["result"]
    assert not result.get("isError"), f"mail_list_folders error: {result}"
    folders = result.get("structuredContent", {}).get("folders", [])
    print(f"  mail_list_folders OK -- {len(folders)} folder(s): {[f['name'] for f in folders]}")

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (AssertionError, KeyError, urllib.error.URLError) as e:
        print(f"SMOKE FAILED: {e}", file=sys.stderr)
        sys.exit(1)
