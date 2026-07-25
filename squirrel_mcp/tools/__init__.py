"""MCP mail tools for Squirrel.

Public API is re-exported here so imports stay stable:
``from squirrel_mcp.tools import register_tools, MailToolHandler``.
"""

from ._common import _current_sub, logger, run_blocking
from .calendar import CalendarToolHandler, register_calendar_tools
from .contacts import ContactsToolHandler, register_contacts_tools
from .handler import MailToolHandler, register_tools

__all__ = [
    "MailToolHandler",
    "register_tools",
    "CalendarToolHandler",
    "register_calendar_tools",
    "ContactsToolHandler",
    "register_contacts_tools",
    "run_blocking",
    "_current_sub",
    "logger",
]
