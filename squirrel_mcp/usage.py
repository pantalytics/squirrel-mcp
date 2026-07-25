"""Usage tracking stub.

No-op in the public package so Squirrel works standalone with zero telemetry.
The private ``squirrel-mcp-admin`` package overrides this with a real tracker
(PostHog) for the hosted, multi-tenant deployment. Keep the signature stable --
admin imports it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def track_event(event: str, properties: Optional[Dict[str, Any]] = None) -> None:
    """No-op. Overridden by the admin package. Never sends anything from OSS."""
    return None
