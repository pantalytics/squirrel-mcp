"""MCP tool handler for Squirrel mail operations.

``MailToolHandler`` is assembled from per-area mixins (folders, query, read,
compose, organize). ``_get_provider`` and ``_track_usage`` are the seams the
private admin package overrides for per-user provider resolution and usage
tracking; standalone they return the single env-configured provider.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

from ..config import SquirrelConfig
from ..error_handling import ValidationError
from ..providers import MailProvider
from ._common import _current_sub, logger
from .mail.compose import ComposeToolsMixin
from .mail.folders import FoldersToolsMixin
from .mail.organize import OrganizeToolsMixin
from .mail.query import QueryToolsMixin
from .mail.read import ReadToolsMixin


class MailToolHandler(
    FoldersToolsMixin,
    QueryToolsMixin,
    ReadToolsMixin,
    ComposeToolsMixin,
    OrganizeToolsMixin,
):
    """Handles MCP tool requests for mail operations."""

    def __init__(
        self,
        app: FastMCP,
        provider: Optional[MailProvider] = None,
        config: Optional[SquirrelConfig] = None,
    ):
        self.app = app
        self.provider = provider
        self.config = config
        self._register_tools()

    async def _get_provider(
        self,
        connection: Optional[str] = None,
        *,
        writes: bool = False,
    ) -> Tuple[MailProvider, str]:
        """Resolve the provider + subject for the current request.

        Admin hook: the private package overrides this to resolve a per-user
        provider from the authenticated subject. Standalone: the single provider.
        """
        if self.provider is not None:
            _current_sub.set("stdio")
            return self.provider, "stdio"
        raise ValidationError("No mail provider available")

    async def _list_accounts(self) -> List[dict]:
        """The email accounts this server can resolve, for ``mail_list_accounts``.

        Admin hook: the private package overrides this to list the workspace's
        configured accounts. Standalone there is exactly one -- the env-configured
        mailbox -- and it is always the default.
        """
        email = ""
        if self.provider is not None:
            email = getattr(self.provider, "email", "") or ""
        if not email and self.config is not None:
            email = self.config.mail_email or ""
        return [{"id": "default", "email": email, "default": True}]

    def _track_usage(self, sub: str, tool_name: str) -> None:
        """Usage-tracking hook. No-op here; overridden by the admin package."""

    def _register_tools(self) -> None:
        # Registration order is the order tools appear to clients.
        self._register_folders_tools()
        self._register_query_tools()
        self._register_read_tools()
        self._register_compose_tools()
        self._register_organize_tools()


def register_tools(
    app: FastMCP,
    provider: Optional[MailProvider] = None,
    config: Optional[SquirrelConfig] = None,
) -> MailToolHandler:
    """Register all Squirrel mail tools with the FastMCP app."""
    handler = MailToolHandler(app, provider=provider, config=config)
    logger.info("Registered Squirrel mail tools")
    return handler
