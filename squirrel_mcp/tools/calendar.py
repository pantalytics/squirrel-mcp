"""Calendar (CalDAV) MCP tools -- calendar_* namespace.

Mirrors the mail handler: a handler with ``_get_provider`` / ``_track_usage`` hooks
(the seams the admin package can override) and the tools registered inline. Write
tools require ``confirm=true``.
"""

from __future__ import annotations

from typing import Optional, Tuple

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..config import SquirrelConfig
from ..error_handling import ValidationError
from ..providers import CalendarProvider
from ..schemas import (
    CalendarList,
    CalendarOut,
    EventDetailOut,
    EventList,
    EventOut,
    EventWriteResult,
)
from ._common import _current_sub, logger, require_confirm, run_blocking

_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


class CalendarToolHandler:
    """Registers the calendar_* tools against a CalendarProvider."""

    def __init__(self, app: FastMCP, provider: CalendarProvider, config: SquirrelConfig):
        self.app = app
        self.provider = provider
        self.config = config
        self._register()

    async def _get_provider(self, *, writes: bool = False) -> Tuple[CalendarProvider, str]:
        if self.provider is None:
            raise ValidationError("No calendar provider available")
        _current_sub.set("stdio")
        return self.provider, "stdio"

    def _track_usage(self, sub: str, tool_name: str) -> None:
        """Overridable usage hook (no-op standalone)."""

    def _register(self):
        @self.app.tool(title="List Calendars", annotations=_READ)
        async def calendar_list_calendars() -> CalendarList:
            """List the calendars in the account. Use an id here as 'calendar' elsewhere."""
            provider, sub = await self._get_provider()
            cals = await run_blocking(provider, provider.list_calendars)
            self._track_usage(sub, "calendar_list_calendars")
            return CalendarList(
                calendars=[CalendarOut(id=c.id, name=c.name, description=c.description) for c in cals]
            )

        @self.app.tool(title="Search Events", annotations=_READ)
        async def calendar_search_events(
            calendar: str,
            start: Optional[str] = None,
            end: Optional[str] = None,
            query: Optional[str] = None,
            limit: Optional[int] = None,
        ) -> EventList:
            """Search a calendar's events in a date window (ISO dates), newest first.

            Defaults to roughly ±6 months when start/end are omitted. ``query`` filters
            on the event title.
            """
            provider, sub = await self._get_provider()
            eff_limit = max(1, min(limit or 50, 200))
            events = await run_blocking(
                provider, provider.search_events, calendar,
                start=start, end=end, query=query, limit=eff_limit,
            )
            self._track_usage(sub, "calendar_search_events")
            return EventList(
                events=[_event_out(e) for e in events], count=len(events), calendar=calendar
            )

        @self.app.tool(title="Read Event", annotations=_READ)
        async def calendar_read_event(calendar: str, uid: str) -> EventDetailOut:
            """Read one event by uid."""
            provider, sub = await self._get_provider()
            e = await run_blocking(provider, provider.get_event, calendar, uid)
            self._track_usage(sub, "calendar_read_event")
            return EventDetailOut(
                uid=e.uid, calendar=e.calendar, summary=e.summary, start=e.start, end=e.end,
                all_day=e.all_day, location=e.location, description=e.description,
                organizer=e.organizer, attendees=e.attendees, status=e.status,
            )

        @self.app.tool(title="Create Event", annotations=_WRITE)
        async def calendar_create_event(
            calendar: str,
            summary: str,
            start: str,
            end: str,
            all_day: bool = False,
            location: Optional[str] = None,
            description: Optional[str] = None,
            confirm: bool = False,
        ) -> EventWriteResult:
            """Create an event (ISO start/end). Requires confirm=true; confirm details first."""
            provider, sub = await self._get_provider(writes=True)
            require_confirm(confirm, "Creating an event")
            uid = await run_blocking(
                provider, provider.create_event, calendar, summary, start, end,
                all_day=all_day, location=location, description=description,
            )
            self._track_usage(sub, "calendar_create_event")
            return EventWriteResult(uid=uid, calendar=calendar, status="Event created")

        @self.app.tool(title="Update Event", annotations=_WRITE)
        async def calendar_update_event(
            calendar: str,
            uid: str,
            summary: Optional[str] = None,
            start: Optional[str] = None,
            end: Optional[str] = None,
            location: Optional[str] = None,
            description: Optional[str] = None,
            confirm: bool = False,
        ) -> EventWriteResult:
            """Update fields of an event. Requires confirm=true."""
            provider, sub = await self._get_provider(writes=True)
            require_confirm(confirm, "Updating an event")
            new_uid = await run_blocking(
                provider, provider.update_event, calendar, uid,
                summary=summary, start=start, end=end, location=location, description=description,
            )
            self._track_usage(sub, "calendar_update_event")
            return EventWriteResult(uid=new_uid, calendar=calendar, status="Event updated")

        @self.app.tool(title="Delete Event", annotations=_WRITE)
        async def calendar_delete_event(
            calendar: str, uid: str, confirm: bool = False
        ) -> EventWriteResult:
            """Delete an event. Requires confirm=true."""
            provider, sub = await self._get_provider(writes=True)
            require_confirm(confirm, "Deleting an event")
            await run_blocking(provider, provider.delete_event, calendar, uid)
            self._track_usage(sub, "calendar_delete_event")
            return EventWriteResult(uid=uid, calendar=calendar, status="Event deleted")


def _event_out(e) -> EventOut:
    return EventOut(
        uid=e.uid, calendar=e.calendar, summary=e.summary,
        start=e.start, end=e.end, all_day=e.all_day, location=e.location,
    )


def register_calendar_tools(
    app: FastMCP, provider: CalendarProvider, config: SquirrelConfig
) -> CalendarToolHandler:
    handler = CalendarToolHandler(app, provider, config)
    logger.info("Registered Squirrel calendar tools")
    return handler
