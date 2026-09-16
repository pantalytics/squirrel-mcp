"""Calendar MCP tools -- calendar_* namespace.

Mirrors the mail handler: a handler with ``_get_provider`` / ``_track_usage`` hooks
(the seams the admin package can override) and the tools registered inline. Write
tools require ``confirm=true``.

CalDAV is one backend, not the only one, and the two things a *meeting* needs
beyond an appointment -- inviting people and having somewhere to meet -- are the
place that shows. Both are asked for on ``calendar_create`` and both are
gated on a capability the provider declares (``supports_attendees`` /
``supports_online_meeting``, read with ``getattr`` so a backend predating them
answers no). A backend that cannot do it is told so plainly, in the same breath
as what will work instead; it is never quietly dropped from the event, because a
caller who thinks they invited five people and invited none is worse off than one
whose tool call refused.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

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
from ._common import _current_sub, as_str_list, logger, require_confirm, run_blocking

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

    async def _join_url(self, provider, calendar: str, uid: str) -> Optional[str]:
        """Read a freshly created event back for its conference link.

        The backend mints the link while creating the event, so a read is the
        only way to it -- and it earns the extra round trip, because "the
        meeting exists" is no use to whoever has to join it. A failure here is
        logged and swallowed on purpose: the event *was* created, and reporting
        the tool call as failed invites a retry that books it twice.
        """
        try:
            event = await run_blocking(provider, provider.get_event, calendar, uid)
            return getattr(event, "join_url", None)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Event %s was created but reading its join link back failed", uid,
                exc_info=True,
            )
            return None

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
        async def calendar_search(
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
            self._track_usage(sub, "calendar_search")
            return EventList(
                events=[_event_out(e) for e in events], count=len(events), calendar=calendar
            )

        @self.app.tool(title="Read Event", annotations=_READ)
        async def calendar_read(calendar: str, uid: str) -> EventDetailOut:
            """Read one event by uid."""
            provider, sub = await self._get_provider()
            e = await run_blocking(provider, provider.get_event, calendar, uid)
            self._track_usage(sub, "calendar_read")
            return EventDetailOut(
                uid=e.uid, calendar=e.calendar, summary=e.summary, start=e.start, end=e.end,
                all_day=e.all_day, location=e.location, description=e.description,
                organizer=e.organizer, attendees=e.attendees, status=e.status,
                join_url=getattr(e, "join_url", None),
            )

        @self.app.tool(title="Create Event", annotations=_WRITE)
        async def calendar_create(
            calendar: str,
            summary: str,
            start: str,
            end: str,
            all_day: bool = False,
            location: Optional[str] = None,
            description: Optional[str] = None,
            attendees: Optional[Any] = None,
            online_meeting: bool = False,
            confirm: bool = False,
        ) -> EventWriteResult:
            """Create an event (ISO start/end). Requires confirm=true; confirm details first.

            ``attendees`` (email addresses) makes it a meeting and sends each of
            them an invitation. ``online_meeting=true`` asks the calendar to add
            a conference link -- a Teams meeting on Outlook / Microsoft 365 --
            and the link comes back as ``join_url``. Not every calendar can do
            either; the tool says so rather than dropping the request. Show the
            attendee list to the user before confirming: this sends mail.
            """
            provider, sub = await self._get_provider(writes=True)
            invitees = _invitation(provider, attendees, online_meeting)
            require_confirm(confirm, "Creating an event")
            uid = await run_blocking(
                provider, provider.create_event, calendar, summary, start, end,
                all_day=all_day, location=location, description=description,
                attendees=invitees, online_meeting=online_meeting,
            )
            self._track_usage(sub, "calendar_create")
            join_url = (
                await self._join_url(provider, calendar, uid) if online_meeting else None
            )
            status = "Event created"
            if invitees:
                status += f"; invitation sent to {len(invitees)} attendee(s)"
            return EventWriteResult(
                uid=uid, calendar=calendar, status=status, join_url=join_url
            )

        @self.app.tool(title="Update Event", annotations=_WRITE)
        async def calendar_update(
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
            self._track_usage(sub, "calendar_update")
            return EventWriteResult(uid=new_uid, calendar=calendar, status="Event updated")

        @self.app.tool(title="Delete Event", annotations=_WRITE)
        async def calendar_delete(
            calendar: str, uid: str, confirm: bool = False
        ) -> EventWriteResult:
            """Delete an event. Requires confirm=true."""
            provider, sub = await self._get_provider(writes=True)
            require_confirm(confirm, "Deleting an event")
            await run_blocking(provider, provider.delete_event, calendar, uid)
            self._track_usage(sub, "calendar_delete")
            return EventWriteResult(uid=uid, calendar=calendar, status="Event deleted")


def _invitation(provider, attendees, online_meeting: bool) -> List[str]:
    """Check the meeting half of a create against what this backend can do.

    Returns the cleaned attendee list. Raises ``ValidationError`` -- with the
    alternative that does work in the same sentence -- rather than let either
    request be dropped on the way to a backend that cannot honour it.
    """
    if online_meeting and not getattr(provider, "supports_online_meeting", False):
        raise ValidationError(
            "This calendar's backend cannot create an online meeting. Create the "
            "event without one and put a meeting link in the description, or use "
            "an Outlook / Microsoft 365 calendar."
        )

    # A list, a bare address or a comma-separated string -- the same three
    # shapes mail recipients arrive in, normalised by the same helper.
    cleaned: List[str] = []
    for address in as_str_list(attendees):
        if "@" not in address:
            raise ValidationError(f"attendees: {address!r} is not an email address")
        # The same address twice is the caller's slip, not worth failing on.
        if address.lower() not in {a.lower() for a in cleaned}:
            cleaned.append(address)

    if cleaned and not getattr(provider, "supports_attendees", False):
        raise ValidationError(
            "This calendar's backend cannot send invitations, so naming attendees "
            "here would add them to the event without telling anyone. Create the "
            "event without attendees and invite people yourself, or use an "
            "Outlook / Microsoft 365 calendar."
        )
    return cleaned


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
