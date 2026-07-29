"""Shared helpers for the tools package."""

from __future__ import annotations

import asyncio
import contextvars
import email.utils
import re
from typing import Any, Callable, Iterable, TypeVar
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


# "Re:" in the languages a mail client is likely to have written it in. Only
# the ASCII "re" is universal; the rest exist so a reply to a reply does not
# grow "Re: Antwort: Re: ..." one hop at a time.
_RE_PREFIX = re.compile(r"^\s*(re|aw|antw|antwort|sv|vs|ref|res|odp|回复)\s*(\[\d+\])?\s*:\s*", re.I)


def reply_subject(subject: str) -> str:
    """The subject a reply to ``subject`` should carry.

    Prefixes "Re: " unless there already is one -- a thread must not collect a
    prefix per hop, and a mail client that threads on the subject (Outlook does,
    as a fallback) treats "Re: Re: x" as a different conversation from "Re: x".
    """
    stripped = (subject or "").strip()
    if not stripped:
        return "Re:"
    if _RE_PREFIX.match(stripped):
        return stripped
    return f"Re: {stripped}"


def bare_addresses(values: Iterable[str]) -> list[str]:
    """Header values like ``Anna <anna@x.eu>`` -> ``anna@x.eu``, de-duplicated.

    A display name is fine in a To header and fatal in an SMTP envelope, so
    anything derived from a message we read gets unwrapped before it is used as
    a recipient. Comparison for de-duplication is case-insensitive, since
    address casing is not meaningful to anyone but the local part's own server.
    """
    out: list[str] = []
    seen: set[str] = set()
    for _name, addr in email.utils.getaddresses([v for v in values if v]):
        addr = addr.strip()
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append(addr)
    return out


def require_confirm(confirm: bool, action: str) -> None:
    """Guard for outgoing/mutating tools: refuse unless explicitly confirmed."""
    if not confirm:
        raise ValidationError(
            f"{action} requires confirm=true. Show the user exactly what will happen "
            f"and get their approval, then call again with confirm=true."
        )
