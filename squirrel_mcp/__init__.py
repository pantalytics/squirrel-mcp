"""Squirrel -- a local, sovereign personal-information hub as an MCP server.

v1 exposes your own mailbox (Soverin, via IMAP/SMTP) to Claude and other MCP
clients, running locally with no mail content leaving your machine. Contacts and
calendar are reserved namespaces for later.
"""

__version__ = "0.1.0"
__author__ = "Pantalytics B.V."
__license__ = "Elastic-2.0"

from .config import SquirrelConfig, load_config
from .server import SquirrelMCPServer

__all__ = [
    "SquirrelMCPServer",
    "SquirrelConfig",
    "load_config",
    "__version__",
]
