"""Entry point for the squirrel-mcp package.

Run via ``python -m squirrel_mcp`` or the ``squirrel-mcp`` console script.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Optional

from .config import load_config
from .server import SERVER_VERSION, SquirrelMCPServer


def main(argv: Optional[list[str]] = None) -> int:
    """Parse args, load config, and run the server with the chosen transport."""
    parser = argparse.ArgumentParser(
        description="Squirrel MCP Server -- your own mail, exposed to AI, locally.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Environment variables (see .env.example):
  SQUIRREL_MAIL_PROVIDER   Backend to use (default: imap)
  SQUIRREL_MAIL_EMAIL      Your mailbox address (required)
  SQUIRREL_MAIL_PASSWORD   Your mailbox password (required)
  SQUIRREL_IMAP_HOST/PORT  Incoming server, e.g. imap.example.com:993 (host required)
  SQUIRREL_SMTP_HOST/PORT  Outgoing server, e.g. smtp.example.com:465 (host required)
  SQUIRREL_MCP_TRANSPORT   stdio (default) or streamable-http
""",
    )
    parser.add_argument("--version", action="version", version=f"squirrel-mcp v{SERVER_VERSION}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Test the mailbox connection (log in + list folders) and exit",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.getenv("SQUIRREL_MCP_TRANSPORT", "stdio"),
        help="Transport type to use (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("SQUIRREL_MCP_HOST", "localhost"),
        help="Host for HTTP transport (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("SQUIRREL_MCP_PORT", "8000")),
        help="Port for HTTP transport (default: 8000)",
    )
    args = parser.parse_args(argv)

    try:
        # CLI flags override the environment before config is loaded.
        os.environ["SQUIRREL_MCP_TRANSPORT"] = args.transport
        os.environ["SQUIRREL_MCP_HOST"] = args.host
        os.environ["SQUIRREL_MCP_PORT"] = str(args.port)

        config = load_config()

        if args.check:
            return _check_connection(config)

        server = SquirrelMCPServer(config)

        if config.transport == "stdio":
            asyncio.run(server.run_stdio())
        elif config.transport == "streamable-http":
            asyncio.run(server.run_http(host=config.host, port=config.port))
        else:
            raise ValueError(f"Unsupported transport: {config.transport}")
        return 0

    except KeyboardInterrupt:
        print("\nServer stopped by user", file=sys.stderr)
        return 0
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print("\nCheck your environment variables or .env file.", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        logging.error("Server error: %s", e, exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _check_connection(config) -> int:
    """Log in, list folders, and report -- the 'Test connection' equivalent."""
    from .providers import MailProviderError, create_mail_provider

    print(
        f"Checking {config.mail_provider} at {config.imap_host}:{config.imap_port} "
        f"as {config.login_username} ({config.imap_security}) ..."
    )
    provider = create_mail_provider(config)
    try:
        provider.connect()
        folders = provider.list_folders()
        print(f"OK -- connected. {len(folders)} folder(s): {[f.name for f in folders]}")
        return 0
    except MailProviderError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    finally:
        provider.disconnect()


if __name__ == "__main__":
    sys.exit(main())
