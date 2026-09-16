"""Attendees and online meetings on ``calendar_create``.

The two things that separate a meeting from an appointment -- people to invite
and somewhere to meet -- are the first calendar features no CalDAV server can
do. So what is pinned here is mostly the *refusal*: a backend that cannot invite
must say so, and the request must never reach the provider stripped of the half
it could not honour. The counterpart, a backend that can, is pinned alongside it
so the gate is a gate and not a wall.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from squirrel_mcp.config import SquirrelConfig
from squirrel_mcp.providers.protocol import CalendarInfo, EventDetail, EventSummary
from squirrel_mcp.server import create_fastmcp_app
from squirrel_mcp.tools import register_calendar_tools


class FakeCalendarProvider:
    """A CalendarProvider over in-memory data, with the capabilities dialled."""

    def __init__(self, *, attendees: bool = False, online: bool = False):
        self.connected = False
        self._attendees = attendees
        self._online = online
        # Every create_event's keyword arguments, so a test can assert what the
        # tool layer actually handed the backend.
        self.created: list = []
        # What a read hands back as the join link, once one has been minted.
        self.join_url: Optional[str] = None
        # Set to have a read-back blow up, standing in for a backend that
        # created the event and then failed to answer for it.
        self.read_fails = False

    @property
    def is_authenticated(self) -> bool:
        return self.connected

    @property
    def supports_attendees(self) -> bool:
        return self._attendees

    @property
    def supports_online_meeting(self) -> bool:
        return self._online

    def connect(self) -> None:
        self.connected = True

    def authenticate(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def list_calendars(self) -> List[CalendarInfo]:
        return [CalendarInfo(id="cal-1", name="Calendar")]

    def search_events(self, calendar: str, **kw) -> List[EventSummary]:
        return []

    def get_event(self, calendar: str, uid: str) -> EventDetail:
        if self.read_fails:
            raise RuntimeError("event read exploded")
        return EventDetail(
            uid=uid, calendar=calendar, summary="Standup",
            start=None, end=None, all_day=False, location=None,
            description=None, organizer=None, attendees=[], status=None,
            join_url=self.join_url,
        )

    def create_event(self, calendar: str, summary: str, start: str, end: str, **kw) -> str:
        self.created.append(kw)
        if kw.get("online_meeting"):
            self.join_url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x"
        return "evt-1"

    def update_event(self, calendar: str, uid: str, **kw) -> str:
        return uid

    def delete_event(self, calendar: str, uid: str) -> None:
        pass


def _app(provider) -> object:
    app = create_fastmcp_app()
    register_calendar_tools(
        app,
        provider,
        SquirrelConfig(
            mail_email="me@x.eu", mail_password="pw",
            imap_host="imap.x.eu", smtp_host="smtp.x.eu",
        ),
    )
    return app


CREATE = {
    "calendar": "cal-1",
    "summary": "Standup",
    "start": "2026-09-01T10:00:00",
    "end": "2026-09-01T10:30:00",
    "confirm": True,
}


# ---- the refusals ---------------------------------------------------------- #
async def test_attendees_are_refused_by_a_backend_that_cannot_invite():
    """Not "invited nobody, reported success" -- the whole point of the gate."""
    provider = FakeCalendarProvider(attendees=False)
    with pytest.raises(Exception) as exc:
        await _app(provider).call_tool(
            "calendar_create", {**CREATE, "attendees": ["a@example.com"]}
        )
    assert "invitation" in str(exc.value).lower()
    assert provider.created == []  # and no event either way


async def test_an_online_meeting_is_refused_by_a_backend_that_mints_none():
    provider = FakeCalendarProvider(online=False)
    with pytest.raises(Exception) as exc:
        await _app(provider).call_tool(
            "calendar_create", {**CREATE, "online_meeting": True}
        )
    assert "online meeting" in str(exc.value).lower()
    assert provider.created == []


async def test_a_backend_predating_the_capabilities_answers_no():
    """``getattr(..., False)``: a provider written before either property
    existed has neither, and must not be mistaken for one that can invite."""

    class OldProvider(FakeCalendarProvider):
        def __getattribute__(self, name):
            if name in ("supports_attendees", "supports_online_meeting"):
                raise AttributeError(name)
            return super().__getattribute__(name)

    provider = OldProvider()
    assert not hasattr(provider, "supports_attendees")

    with pytest.raises(Exception) as exc:
        await _app(provider).call_tool(
            "calendar_create", {**CREATE, "attendees": ["a@example.com"]}
        )
    assert "invitation" in str(exc.value).lower()
    assert provider.created == []


async def test_a_plain_event_still_works_on_a_backend_that_can_do_neither():
    """The gate is on the meeting half only; an appointment is unaffected."""
    provider = FakeCalendarProvider()
    await _app(provider).call_tool("calendar_create", CREATE)
    assert provider.created == [
        {
            "all_day": False,
            "location": None,
            "description": None,
            "attendees": [],
            "online_meeting": False,
        }
    ]


# ---- the capable backend --------------------------------------------------- #
async def test_attendees_reach_the_provider_and_the_result_says_so():
    provider = FakeCalendarProvider(attendees=True)
    result = await _app(provider).call_tool(
        "calendar_create",
        {**CREATE, "attendees": ["a@example.com", "b@example.com"]},
    )
    assert provider.created[0]["attendees"] == ["a@example.com", "b@example.com"]
    # The caller has to be able to tell the user that mail went out.
    assert "invitation sent to 2" in str(result)


@pytest.mark.parametrize(
    "sent, expected",
    [
        ("solo@example.com", ["solo@example.com"]),
        ("a@example.com, b@example.com", ["a@example.com", "b@example.com"]),
    ],
)
async def test_attendees_may_arrive_the_way_recipients_do(sent, expected):
    """A bare address or a comma-separated string, the shapes an MCP client
    actually sends -- normalised by the same helper mail recipients use."""
    provider = FakeCalendarProvider(attendees=True)
    await _app(provider).call_tool("calendar_create", {**CREATE, "attendees": sent})
    assert provider.created[0]["attendees"] == expected


async def test_attendees_are_trimmed_and_deduped_case_insensitively():
    provider = FakeCalendarProvider(attendees=True)
    await _app(provider).call_tool(
        "calendar_create",
        {**CREATE, "attendees": [" a@example.com ", "A@Example.com", ""]},
    )
    assert provider.created[0]["attendees"] == ["a@example.com"]


async def test_something_that_is_not_an_address_is_refused():
    provider = FakeCalendarProvider(attendees=True)
    with pytest.raises(Exception) as exc:
        await _app(provider).call_tool(
            "calendar_create", {**CREATE, "attendees": ["Anna Jansen"]}
        )
    assert "not an email address" in str(exc.value).lower()
    assert provider.created == []


async def test_an_online_meeting_comes_back_with_its_join_link():
    """The link is the deliverable: an event nobody can join is half an answer."""
    provider = FakeCalendarProvider(attendees=True, online=True)
    result = await _app(provider).call_tool(
        "calendar_create",
        {**CREATE, "attendees": ["a@example.com"], "online_meeting": True},
    )
    assert provider.created[0]["online_meeting"] is True
    assert "teams.microsoft.com" in str(result)


async def test_a_read_back_that_fails_does_not_fail_the_creation():
    """The event exists. Reporting failure invites a retry that books it twice."""
    provider = FakeCalendarProvider(online=True)
    provider.read_fails = True
    result = await _app(provider).call_tool(
        "calendar_create", {**CREATE, "online_meeting": True}
    )
    assert provider.created[0]["online_meeting"] is True
    assert "Event created" in str(result)


async def test_creating_still_needs_confirmation():
    """A meeting sends mail, so the confirm gate matters more here, not less."""
    provider = FakeCalendarProvider(attendees=True)
    with pytest.raises(Exception) as exc:
        await _app(provider).call_tool(
            "calendar_create",
            {**{k: v for k, v in CREATE.items() if k != "confirm"},
             "attendees": ["a@example.com"]},
        )
    assert "confirm" in str(exc.value).lower()
    assert provider.created == []


async def test_reading_an_event_reports_its_join_link():
    provider = FakeCalendarProvider(online=True)
    provider.join_url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_y"
    result = await _app(provider).call_tool(
        "calendar_read", {"calendar": "cal-1", "uid": "evt-1"}
    )
    assert "meetup-join" in str(result)
