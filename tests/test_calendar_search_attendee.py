"""``attendee`` on ``calendar_search``: finding events by who is on them.

Until this, ``calendar_search`` filtered on the title only, so "my sessions
with the coach" meant paging a six-month window and reading every event. Three
things are pinned. The tool forwards the filter to a backend that names it and
*refuses* for one that does not, because a backend that silently dropped it
would answer "every event" and call it the match. The CalDAV backend matches
the address, the ``CN`` display name and the organizer, since the person who
invited you is a participant whether or not the server files them under
ATTENDEE. And the match is a case-insensitive substring, so one word of a name
is enough.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

import pytest
from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent
from icalendar import vCalAddress, vText

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import CalendarInfo, EventSummary
from squirrel_mcp.providers.soverin.calendar import SoverinCalendarProvider
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_calendar_tools


# ---- the tool layer -------------------------------------------------------- #
class _Provider:
    """Records what ``search_events`` was asked, with or without the filter."""

    def __init__(self, *, takes_attendee: bool):
        self.calls: list = []
        if takes_attendee:
            self.search_events = self._with
        else:
            self.search_events = self._without

    is_authenticated = True

    def connect(self): ...
    def disconnect(self): ...
    def authenticate(self): ...

    def list_calendars(self) -> List[CalendarInfo]:
        return [CalendarInfo(id="cal-1", name="Calendar")]

    def _with(self, calendar, *, start=None, end=None, query=None,
              attendee: Optional[str] = None, limit=50) -> List[EventSummary]:
        self.calls.append({"query": query, "attendee": attendee})
        return []

    def _without(self, calendar, *, start=None, end=None, query=None,
                 limit=50) -> List[EventSummary]:
        self.calls.append({"query": query})
        return []


def _app(provider):
    app = create_fastmcp_app()
    register_calendar_tools(
        app, provider,
        SquirrelConfig(mail_email="me@x.eu", mail_password="pw",
                       imap_host="imap.x.eu", smtp_host="smtp.x.eu"),
    )
    return app


async def test_the_filter_reaches_a_backend_that_names_it():
    provider = _Provider(takes_attendee=True)
    await _app(provider).call_tool(
        "calendar_search", {"calendar": "cal-1", "attendee": "iris", "query": "coach"}
    )
    assert provider.calls == [{"query": "coach", "attendee": "iris"}]


async def test_a_backend_predating_the_filter_is_refused_not_bypassed():
    provider = _Provider(takes_attendee=False)
    with pytest.raises(Exception) as exc:
        await _app(provider).call_tool(
            "calendar_search", {"calendar": "cal-1", "attendee": "iris"}
        )
    assert "attendee" in str(exc.value).lower()
    assert provider.calls == []  # not forwarded minus the filter


async def test_without_the_filter_an_old_backend_still_answers():
    """The refusal is for the filter, not for the backend."""
    provider = _Provider(takes_attendee=False)
    await _app(provider).call_tool("calendar_search", {"calendar": "cal-1", "query": "x"})
    assert provider.calls == [{"query": "x"}]


# ---- the CalDAV backend ---------------------------------------------------- #
def _event(uid, summary, start, *, attendees=(), organizer=None):
    ev = IEvent()
    ev.add("uid", uid)
    ev.add("summary", summary)
    ev.add("dtstart", start)
    for address, cn in attendees:
        a = vCalAddress(f"mailto:{address}")
        if cn:
            a.params["CN"] = vText(cn)
        ev.add("attendee", a, encode=0)
    if organizer:
        o = vCalAddress(f"mailto:{organizer[0]}")
        if organizer[1]:
            o.params["CN"] = vText(organizer[1])
        ev.add("organizer", o, encode=0)
    cal = ICalendar()
    cal.add_component(ev)
    # The ``caldav`` library hands back objects whose ``icalendar_component``
    # is the parsed VEVENT; parsing our own output is what a server's would be.
    parsed = ICalendar.from_ical(cal.to_ical())

    class Obj:
        icalendar_component = parsed.walk("VEVENT")[0]

    return Obj()


class _FakeCalendar:
    url = "https://dav.example/cal/"

    def __init__(self, events):
        self._events = events

    def search(self, **kw):
        return self._events


def _provider(events) -> SoverinCalendarProvider:
    p = SoverinCalendarProvider(
        SquirrelConfig(mail_email="me@x.eu", mail_password="pw",
                       imap_host="imap.x.eu", smtp_host="smtp.x.eu",
                       caldav_url="https://dav.example/")
    )
    p._cache = {_FakeCalendar.url: _FakeCalendar(events)}
    return p


D = dt.datetime(2026, 9, 1, 10, 0)
EVENTS = [
    _event("1", "Coaching", D, attendees=[("iris@example.com", "Iris van 't Klooster")]),
    _event("2", "Standup", D + dt.timedelta(days=1), attendees=[("bob@example.com", None)]),
    _event("3", "Review", D + dt.timedelta(days=2), organizer=("iris@example.com", "Iris")),
    _event("4", "Lunch", D + dt.timedelta(days=3)),
]


@pytest.mark.parametrize(
    "needle, expected",
    [
        ("iris", {"1", "3"}),  # one word of a display name, organizer included
        ("klooster", {"1"}),  # the CN, not only the address
        ("BOB@EXAMPLE.COM", {"2"}),  # the address, case-insensitively
        ("nobody", set()),
    ],
)
def test_caldav_matches_address_display_name_and_organizer(needle, expected):
    found = _provider(EVENTS).search_events(_FakeCalendar.url, attendee=needle)
    assert {e.uid for e in found} == expected


def test_caldav_combines_the_attendee_filter_with_the_title_query():
    found = _provider(EVENTS).search_events(
        _FakeCalendar.url, attendee="iris", query="review"
    )
    assert [e.uid for e in found] == ["3"]


def test_caldav_without_the_filter_is_unchanged():
    found = _provider(EVENTS).search_events(_FakeCalendar.url)
    assert [e.uid for e in found] == ["4", "3", "2", "1"]  # newest first, nothing dropped
