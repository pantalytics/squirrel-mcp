"""Shared helpers for the tools package."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any, Callable, TypeVar
from weakref import WeakKeyDictionary

from ..error_handling import (
    AuthenticationError,
    NotFoundError,
    SystemError,
    ValidationError,
)
from ..logging_config import get_logger
from ..providers import ProviderAuthError, ProviderError, ProviderNotFoundError

logger = get_logger("squirrel_mcp.tools")

# Cap on bytes returned inline for an attachment (base64 inflates ~33%).
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024

# Contextvar carrying the current subject from the handler to helpers. Standalone
# is always "stdio"; the admin package sets a real per-user subject.
_current_sub: contextvars.ContextVar[str] = contextvars.ContextVar("_current_sub", default="stdio")

T = TypeVar("T")

# One asyncio.Lock per provider object: IMAP/SMTP transports are not safe for
# concurrent use of the same connection, so calls to the same provider take turns
# while different providers (multi-tenant) run in parallel. WeakKeyDictionary lets
# the lock vanish when the provider is garbage-collected.
_provider_locks: "WeakKeyDictionary[Any, asyncio.Lock]" = WeakKeyDictionary()


def _lock_for(provider: Any) -> asyncio.Lock:
    lock = _provider_locks.get(provider)
    if lock is None:
        lock = asyncio.Lock()
        _provider_locks[provider] = lock
    return lock


async def run_blocking(provider: Any, func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run a blocking provider call off the event loop, serialized per provider.

    Wraps ``asyncio.to_thread`` and holds the per-provider lock for the duration,
    so one IMAP/SMTP connection is never touched by two threads at once. Provider
    errors are translated to sanitized MCP errors.
    """
    async with _lock_for(provider):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except ProviderAuthError as e:
            raise AuthenticationError(str(e)) from e
        except ProviderNotFoundError as e:
            raise NotFoundError(str(e)) from e
        except ProviderError as e:
            raise SystemError(str(e)) from e


def as_str_list(value: Any) -> list[str]:
    """Coerce a tool argument into a clean list of strings.

    MCP clients pass recipient/uid arguments as either a JSON list or a single
    comma-separated string; accept both. Empty entries are dropped.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = [str(v) for v in value if v is not None]
    else:
        parts = [str(value)]
    return [p.strip() for p in parts if p and p.strip()]


def require_confirm(confirm: bool, action: str) -> None:
    """Guard for outgoing/mutating tools: refuse unless explicitly confirmed."""
    if not confirm:
        raise ValidationError(
            f"{action} requires confirm=true. Show the user exactly what will happen "
            f"and get their approval, then call again with confirm=true."
        )
