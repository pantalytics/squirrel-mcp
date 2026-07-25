"""Squirrel MCP server.

FastMCP app + lifecycle: build the app, connect the mail provider, register the
tools, serve over stdio or streamable-http. ``create_fastmcp_app`` is the single
source of truth for FastMCP construction and is the seam the private admin package
reuses for its multi-tenant entry point.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import SquirrelConfig, get_config
from .error_handling import ConfigurationError, ErrorContext, error_handler
from .knowledge import SERVER_INSTRUCTIONS
from .logging_config import get_logger, logging_config, perf_logger
from .providers import (
    CalendarProvider,
    ContactsProvider,
    MailProvider,
    ProviderError,
    create_calendar_provider,
    create_contacts_provider,
    create_mail_provider,
)
from .tools import register_calendar_tools, register_contacts_tools, register_tools

logger = get_logger(__name__)

# Must match __version__ in __init__.py and version in pyproject.toml.
SERVER_VERSION = "0.1.0"
GIT_COMMIT = os.environ.get("GIT_COMMIT", "unknown")


def create_fastmcp_app(
    *, auth=None, token_verifier=None, extra_instructions: str | None = None
) -> FastMCP:
    """Create the FastMCP app with Squirrel's canonical settings.

    Single source of truth for FastMCP construction -- used by ``SquirrelMCPServer``
    and by the private admin package's multi-tenant entry point. ``stateless_http``
    so any replica can serve any request. ``extra_instructions`` is appended to the
    handshake instructions (the multi-tenant layer uses it to teach the client about
    choosing between several connected mailboxes).
    """
    instructions = SERVER_INSTRUCTIONS
    if extra_instructions:
        instructions = f"{instructions}\n{extra_instructions}"
    return FastMCP(
        name="squirrel-mcp",
        instructions=instructions,
        auth=auth,
        token_verifier=token_verifier,
        stateless_http=True,
        json_response=True,
    )


class SquirrelMCPServer:
    """Main MCP server: owns the FastMCP app and the mail provider connection."""

    def __init__(self, config: Optional[SquirrelConfig] = None):
        self.config = config or get_config()
        logging_config.setup()

        self.provider: Optional[MailProvider] = None
        self.calendar_provider: Optional[CalendarProvider] = None
        self.contacts_provider: Optional[ContactsProvider] = None
        self.tool_handler = None

        self.app = create_fastmcp_app()
        logger.info("Initialized Squirrel MCP Server v%s", SERVER_VERSION)

    def _ensure_connection(self) -> None:
        """Create and connect the mail provider (surfaces auth errors early)."""
        if self.provider is not None:
            return
        try:
            logger.info("Connecting mail provider %r...", self.config.mail_provider)
            with perf_logger.track_operation("connection_setup"):
                provider = create_mail_provider(self.config)
                provider.connect()
            self.provider = provider
            logger.info(
                "Connected to %s (%s)", self.config.imap_host, self.config.mail_email
            )
        except (ProviderError, ConfigurationError):
            raise
        except Exception as e:  # noqa: BLE001 - funnel through the error handler
            error_handler.handle_error(e, context=ErrorContext(operation="connection_setup"))

    def _cleanup_connection(self) -> None:
        for prov in (self.provider, self.calendar_provider, self.contacts_provider):
            if prov is not None:
                try:
                    prov.disconnect()
                except Exception as e:  # noqa: BLE001 - best effort
                    logger.error("Error closing provider: %s", e)
        self.provider = None
        self.calendar_provider = None
        self.contacts_provider = None
        self.tool_handler = None

    def _register_tools(self) -> None:
        """Register the tool families for every configured pillar.

        Mail is always present. Contacts (CardDAV) and calendar (CalDAV) are
        registered when their URL is configured; those providers connect lazily
        on first use, so a DAV outage doesn't block mail from starting.
        """
        self.tool_handler = register_tools(self.app, self.provider, self.config)
        pillars = ["mail"]
        if self.config.contacts_enabled:
            self.contacts_provider = create_contacts_provider(self.config)
            register_contacts_tools(self.app, self.contacts_provider, self.config)
            pillars.append("contacts")
        if self.config.calendar_enabled:
            self.calendar_provider = create_calendar_provider(self.config)
            register_calendar_tools(self.app, self.calendar_provider, self.config)
            pillars.append("calendar")
        logger.info("Registered Squirrel tools for pillars: %s", ", ".join(pillars))

    async def run_stdio(self) -> None:
        """Run over stdio (the default; used by Claude Desktop / Claude Code)."""
        try:
            with perf_logger.track_operation("server_startup"):
                self._ensure_connection()
                self._register_tools()
            logger.info("Starting MCP server with stdio transport...")
            await self.app.run_stdio_async()
        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
        except (ProviderError, ConfigurationError):
            raise
        except Exception as e:  # noqa: BLE001
            error_handler.handle_error(e, context=ErrorContext(operation="server_run"))
        finally:
            self._cleanup_connection()

    async def run_http(self, host: str = "localhost", port: int = 8000) -> None:
        """Run over streamable HTTP. Unauthenticated -- put auth in front of it."""
        try:
            with perf_logger.track_operation("server_startup"):
                self._ensure_connection()
                self._register_tools()
            logger.info("Starting MCP server with HTTP transport on %s:%s...", host, port)

            self.app.settings.host = host
            self.app.settings.port = port
            if host == "0.0.0.0":  # noqa: S104 - explicit opt-in for LAN exposure
                self.app.settings.transport_security = TransportSecuritySettings(
                    enable_dns_rebinding_protection=False
                )

            asgi_app = self.app.streamable_http_app()

            # Opt-in permissive CORS for local dev/testing (e.g. the Playwright
            # test console on another origin). Off by default; never enable in
            # production without restricting origins.
            if os.getenv("SQUIRREL_DEV_CORS", "").lower() == "true":
                from starlette.middleware.cors import CORSMiddleware

                asgi_app = CORSMiddleware(
                    asgi_app,
                    allow_origins=["*"],
                    allow_methods=["*"],
                    allow_headers=["*"],
                    expose_headers=["mcp-session-id"],
                )
                logger.warning("SQUIRREL_DEV_CORS=true -- permissive CORS enabled (dev only)")

            import uvicorn

            uvicorn_config = uvicorn.Config(
                asgi_app, host=host, port=port, log_level=self.config.log_level.lower()
            )
            await uvicorn.Server(uvicorn_config).serve()
        except KeyboardInterrupt:
            logger.info("Server interrupted by user")
        except (ProviderError, ConfigurationError):
            raise
        except Exception as e:  # noqa: BLE001
            error_handler.handle_error(e, context=ErrorContext(operation="server_run_http"))
        finally:
            self._cleanup_connection()

    def get_health_status(self) -> Dict[str, Any]:
        """Health snapshot for monitoring."""
        is_connected = bool(self.provider and self.provider.is_authenticated)
        return {
            "status": "healthy" if is_connected else "unhealthy",
            "version": SERVER_VERSION,
            "git_commit": GIT_COMMIT,
            "connection": {
                "connected": is_connected,
                "provider": self.config.mail_provider,
                "imap_host": self.config.imap_host,
            },
            "error_metrics": error_handler.get_metrics(),
            "recent_errors": error_handler.get_recent_errors(limit=5),
        }
