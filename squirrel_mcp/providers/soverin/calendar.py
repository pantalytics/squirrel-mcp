"""Calendar (CalDAV) backend, built on the mature ``caldav`` library.

Only this module speaks CalDAV. It returns the transport-neutral dataclasses from
``providers.protocol`` so the tool layer stays backend-agnostic. Same shape as the
mail backend: a thin adapter over a proven library.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import List, Optional

import caldav
from caldav.lib.error import AuthorizationError, NotFoundError
from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent

from ...config import SquirrelConfig
from ...logging_config import get_logger
from ..protocol import (
    CalendarInfo,
    EventDetail,
    EventSummary,
    ProviderAuthError,
    ProviderError,
    ProviderNotFoundError,
)

logger = get_logger(__name__)


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _is_all_day(value) -> bool:
    # A date (not datetime) means an all-day event.
    return isinstance(value, dt.date) and not isinstance(value, dt.datetime)


def _parse_dt(value: str):
    """Parse an ISO string into a date (all-day) or datetime."""
    try:
        if len(value) == 10:  # YYYY-MM-DD
            return dt.date.fromisoformat(value)
        return dt.datetime.fromisoformat(value)
    except ValueError as e:
        raise ProviderError(f"Invalid ISO date/time: {value!r}") from e


class SoverinCalendarProvider:
    """CalendarProvider backed by CalDAV (Soverin / any RFC-compliant server)."""

    def __init__(self, config: SquirrelConfig):
        self._url = config.caldav_url
        self._username = config.login_username
        self._password = config.mail_password
        self._verify = config.tls_verify
        self._client: Optional[caldav.DAVClient] = None
        self._principal = None
        self._cache: dict = {}  # url -> caldav.Calendar

    @property
    def is_authenticated(self) -> bool:
        return self._principal is not None

    def connect(self) -> None:
        self._client = caldav.DAVClient(
            url=self._url,
            username=self._username,
            password=self._password,
            ssl_verify_cert=self._verify,
        )
        try:
            self._principal = self._client.principal()
        except AuthorizationError as e:
            raise ProviderAuthError(f"CalDAV login failed: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Cannot connect to CalDAV server: {e}") from e

    def authenticate(self) -> None:
        if self._principal is None:
            self.connect()

    def disconnect(self) -> None:
        self._principal = None
        self._client = None
        self._cache.clear()

    def _calendars(self) -> List[caldav.Calendar]:
        self.authenticate()
        cals = self._principal.calendars()
        self._cache = {str(c.url): c for c in cals}
        return cals

    def _calendar_by_id(self, calendar: str) -> caldav.Calendar:
        if calendar in self._cache:
            return self._cache[calendar]
        for c in self._calendars():
            if str(c.url) == calendar or str(c.url).rstrip("/").endswith(calendar.rstrip("/")):
                return c
        raise ProviderNotFoundError(f"Calendar not found: {calendar}")

    # ---- reads ----------------------------------------------------------- #
    def list_calendars(self) -> List[CalendarInfo]:
        out = []
        for c in self._calendars():
            try:
                name = c.get_display_name()
            except Exception:  # noqa: BLE001
                name = str(c.url).rstrip("/").rsplit("/", 1)[-1]
            out.append(CalendarInfo(id=str(c.url), name=name or "(unnamed)"))
        return out

    def search_events(
        self,
        calendar: str,
        *,
        start: Optional[str] = None,
        end: Optional[str] = None,
        query: Optional[str] = None,
        limit: int = 50,
    ) -> List[EventSummary]:
        cal = self._calendar_by_id(calendar)
        now = dt.datetime.now(dt.timezone.utc)
        s = _parse_dt(start) if start else now - dt.timedelta(days=180)
        e = _parse_dt(end) if end else now + dt.timedelta(days=180)
        s = dt.datetime.combine(s, dt.time.min) if not isinstance(s, dt.datetime) else s
        e = dt.datetime.combine(e, dt.time.max) if not isinstance(e, dt.datetime) else e

        try:
            found = cal.search(start=s, end=e, event=True, expand=False)
        except Exception as e2:  # noqa: BLE001
            raise ProviderError(f"Event search failed: {e2}") from e2

        summaries = [self._to_summary(obj, calendar) for obj in found]
        if query:
            q = query.lower()
            summaries = [x for x in summaries if q in (x.summary or "").lower()]
        summaries.sort(key=lambda x: x.start or "", reverse=True)
        return summaries[:limit]

    def get_event(self, calendar: str, uid: str) -> EventDetail:
        cal = self._calendar_by_id(calendar)
        try:
            obj = cal.event_by_uid(uid)
        except NotFoundError as e:
            raise ProviderNotFoundError(f"Event {uid} not found in {calendar}") from e
        comp = obj.icalendar_component
        start = comp.get("dtstart")
        end = comp.get("dtend")
        attendees = comp.get("attendee")
        if attendees and not isinstance(attendees, list):
            attendees = [attendees]
        return EventDetail(
            uid=uid,
            calendar=calendar,
            summary=str(comp.get("summary", "")),
            start=_iso(start.dt) if start else None,
            end=_iso(end.dt) if end else None,
            all_day=_is_all_day(start.dt) if start else False,
            location=str(comp.get("location")) if comp.get("location") else None,
            description=str(comp.get("description")) if comp.get("description") else None,
            organizer=str(comp.get("organizer")) if comp.get("organizer") else None,
            attendees=[str(a) for a in (attendees or [])],
            status=str(comp.get("status")) if comp.get("status") else None,
        )

    # ---- writes ---------------------------------------------------------- #
    def create_event(
        self,
        calendar: str,
        summary: str,
        start: str,
        end: str,
        *,
        all_day: bool = False,
        location: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        cal = self._calendar_by_id(calendar)
        uid = str(uuid.uuid4())
        ical = ICalendar()
        ical.add("prodid", "-//Squirrel MCP//EN")
        ical.add("version", "2.0")
        ev = IEvent()
        ev.add("uid", uid)
        ev.add("summary", summary)
        ev.add("dtstamp", dt.datetime.now(dt.timezone.utc))
        ev.add("dtstart", _parse_dt(start))
        ev.add("dtend", _parse_dt(end))
        if location:
            ev.add("location", location)
        if description:
            ev.add("description", description)
        ical.add_component(ev)
        try:
            cal.save_event(ical.to_ical().decode())
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Create event failed: {e}") from e
        return uid

    def update_event(
        self,
        calendar: str,
        uid: str,
        *,
        summary: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
    ) -> str:
        cal = self._calendar_by_id(calendar)
        try:
            obj = cal.event_by_uid(uid)
        except NotFoundError as e:
            raise ProviderNotFoundError(f"Event {uid} not found in {calendar}") from e
        comp = obj.icalendar_component
        if summary is not None:
            comp["summary"] = summary
        if location is not None:
            comp["location"] = location
        if description is not None:
            comp["description"] = description
        if start is not None:
            comp.pop("dtstart", None)
            comp.add("dtstart", _parse_dt(start))
        if end is not None:
            comp.pop("dtend", None)
            comp.add("dtend", _parse_dt(end))
        try:
            obj.save()
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Update event failed: {e}") from e
        return uid

    def delete_event(self, calendar: str, uid: str) -> None:
        cal = self._calendar_by_id(calendar)
        try:
            obj = cal.event_by_uid(uid)
        except NotFoundError as e:
            raise ProviderNotFoundError(f"Event {uid} not found in {calendar}") from e
        obj.delete()

    # ---- helpers --------------------------------------------------------- #
    @staticmethod
    def _to_summary(obj, calendar: str) -> EventSummary:
        comp = obj.icalendar_component
        start = comp.get("dtstart")
        end = comp.get("dtend")
        return EventSummary(
            uid=str(comp.get("uid", "")),
            calendar=calendar,
            summary=str(comp.get("summary", "")),
            start=_iso(start.dt) if start else None,
            end=_iso(end.dt) if end else None,
            all_day=_is_all_day(start.dt) if start else False,
            location=str(comp.get("location")) if comp.get("location") else None,
        )
