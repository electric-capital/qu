"""Tests for the edit_calendar_event action_request handler.

Covers ``validate_params`` (unknown keys, required target fields,
start/end shapes, attendees, the at-least-one-change rule),
``validate_against_upstream`` (live ``expected_updated`` verification,
404 / cancelled / no-op rejections, merged start-end ordering,
``current_event`` capture, transient-failure fallthrough),
``render_preview`` (before/after values, description line diff,
recurrence scope, attendee add/remove), and ``execute`` (scope pre-check,
approve-time re-verification, the PATCH body incl. all-day switching and
attendee record preservation) via a mocked ``make_authenticated_request``.
"""

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


def _handler():
    from chat.action_request_types.edit_calendar_event import EditCalendarEventHandler
    return EditCalendarEventHandler()


UPDATED = "2026-03-10T18:22:41.512Z"


def _live_event(**overrides):
    event = {
        "id": "abc123",
        "status": "confirmed",
        "summary": "Team sync",
        "updated": UPDATED,
        "htmlLink": "https://calendar.google.com/event?eid=abc",
        "start": {"dateTime": "2026-03-18T16:00:00-07:00", "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": "2026-03-18T16:30:00-07:00", "timeZone": "America/Los_Angeles"},
        "attendees": [
            {"email": "alice@example.com", "responseStatus": "accepted"},
            {"email": "bob@example.com", "responseStatus": "needsAction", "optional": True},
        ],
        "location": "Zoom",
        "description": "Weekly sync\nAgenda: roadmap",
    }
    event.update(overrides)
    return event


def _params(**overrides):
    params = {
        "event_id": "abc123",
        "calendar_id": "primary",
        "expected_updated": UPDATED,
        "start": "2026-03-18T17:00:00-07:00",
        "end": "2026-03-18T17:30:00-07:00",
    }
    params.update(overrides)
    return params


def _user(scopes=("https://www.googleapis.com/auth/calendar.events",), connected=True):
    if not connected:
        return {"id": 1, "google_services_oauth": {}}
    return {
        "id": 1,
        "google_services_oauth": {"access_token": "tok", "scopes": list(scopes)},
    }


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload


def _fake_calendar_api(monkeypatch, *, event=None, get_status=200, patch_status=200,
                       captured=None, raise_on_get=None):
    event = _live_event() if event is None else event

    async def _fake_request(client, user, method, url, **kwargs):
        if captured is not None:
            captured.append({"method": method, "url": url, "kwargs": kwargs})
        if "/calendarList/" in url:
            return _FakeResponse({"summary": "Work"})
        if method == "GET":
            if raise_on_get is not None:
                raise raise_on_get
            return _FakeResponse(event if get_status == 200 else {}, status_code=get_status)
        if method == "PATCH":
            merged = dict(event)
            merged.update(kwargs.get("json") or {})
            merged["updated"] = "2026-03-11T00:00:00.000Z"
            return _FakeResponse(merged if patch_status == 200 else
                                 {"error": {"message": "boom"}}, status_code=patch_status)
        return _FakeResponse({}, status_code=500)

    monkeypatch.setattr(
        "chat.action_request_types.edit_calendar_event.make_authenticated_request",
        _fake_request,
    )
    # _resolve_calendar_name lives in the create module and opens its own
    # client; route it too so enrich_params_for_preview is exercised.
    monkeypatch.setattr(
        "chat.action_request_types.create_calendar_invite.make_authenticated_request",
        _fake_request,
    )


# ---------------------------------------------------------------------------
# Metadata / registration
# ---------------------------------------------------------------------------


def test_handler_metadata():
    from db.models import ActionRequestType

    handler = _handler()
    assert handler.type_name == ActionRequestType.EDIT_CALENDAR_EVENT
    assert handler.display_name == "Edit Calendar Event"
    assert handler.approve_label == "Update"
    assert handler.resolved_label == "Updated"


def test_handler_is_registered():
    from chat.action_request_types import get_handler

    assert get_handler("edit_calendar_event") is not None


# ---------------------------------------------------------------------------
# validate_params
# ---------------------------------------------------------------------------


def test_validate_rejects_unknown_and_server_injected_keys():
    handler = _handler()
    with pytest.raises(ValueError, match="Unknown parameter for edit_calendar_event"):
        handler.validate_params(_params(bogus=1))
    with pytest.raises(ValueError, match="Unknown parameter"):
        handler.validate_params(_params(current_event={"summary": "x"}))
    with pytest.raises(ValueError, match="Unknown parameter"):
        handler.validate_params(_params(calendar_name="Work"))


def test_validate_requires_event_id_and_expected_updated():
    handler = _handler()
    with pytest.raises(ValueError, match="event_id"):
        handler.validate_params({"expected_updated": UPDATED, "summary": "x"})
    with pytest.raises(ValueError, match="expected_updated"):
        handler.validate_params({"event_id": "abc", "summary": "x"})
    with pytest.raises(ValueError, match="RFC3339"):
        handler.validate_params({"event_id": "abc", "expected_updated": "yesterday", "summary": "x"})


def test_validate_requires_at_least_one_change():
    handler = _handler()
    with pytest.raises(ValueError, match="No changes supplied"):
        handler.validate_params({"event_id": "abc", "expected_updated": UPDATED})


def test_validate_normalizes_target_and_times():
    handler = _handler()
    validated = handler.validate_params(_params(
        calendar_id="  ", summary="  Renamed ", location=None, description="  notes ",
        attendees=["a@example.com", " b@example.com ", "a@example.com", ""],
    ))
    assert validated["calendar_id"] == "primary"
    assert validated["summary"] == "Renamed"
    assert validated["location"] == ""  # explicit clear
    assert validated["description"] == "notes"
    assert validated["attendees"] == ["a@example.com", "b@example.com"]
    assert validated["start"] == "2026-03-18T17:00:00-07:00"


def test_validate_naive_datetime_needs_time_zone():
    handler = _handler()
    with pytest.raises(ValueError, match="timezone offset or provide time_zone"):
        handler.validate_params(_params(start="2026-03-18T17:00:00", end="2026-03-18T17:30:00"))
    validated = handler.validate_params(_params(
        start="2026-03-18T17:00:00", end="2026-03-18T17:30:00", time_zone="America/New_York",
    ))
    assert validated["start"].endswith("-04:00")
    assert validated["time_zone"] == "America/New_York"


def test_validate_rejects_bad_orders_and_mixed_kinds():
    handler = _handler()
    with pytest.raises(ValueError, match="end must be after start"):
        handler.validate_params(_params(end="2026-03-18T16:00:00-07:00"))
    with pytest.raises(ValueError, match="both be all-day"):
        handler.validate_params(_params(start="2026-03-18"))
    with pytest.raises(ValueError, match="exclusive"):
        handler.validate_params(_params(start="2026-03-18", end="2026-03-18"))
    validated = handler.validate_params(_params(start="2026-03-18", end="2026-03-19"))
    assert validated["start"] == "2026-03-18"


def test_validate_rejects_bad_attendees_and_empty_summary():
    handler = _handler()
    with pytest.raises(ValueError, match="valid email"):
        handler.validate_params(_params(attendees=["not-an-email"]))
    with pytest.raises(ValueError, match="list of email"):
        handler.validate_params(_params(attendees="a@example.com"))
    with pytest.raises(ValueError, match="summary must be a non-empty"):
        handler.validate_params(_params(summary="  "))


# ---------------------------------------------------------------------------
# validate_against_upstream
# ---------------------------------------------------------------------------


def test_upstream_captures_current_event(monkeypatch):
    _fake_calendar_api(monkeypatch)
    handler = _handler()
    params = handler.validate_params(_params())
    out = _run(handler.validate_against_upstream(params, _user()))
    current = out["current_event"]
    assert current["summary"] == "Team sync"
    assert current["start"] == "2026-03-18T16:00:00-07:00"
    assert current["attendees"] == ["alice@example.com", "bob@example.com"]
    assert current["updated"] == UPDATED
    assert current["recurring_series"] is False
    assert "current_event" not in params  # input not mutated


def test_upstream_rejects_stale_expected_updated(monkeypatch):
    _fake_calendar_api(monkeypatch, event=_live_event(updated="2026-03-11T09:00:00.000Z"))
    handler = _handler()
    params = handler.validate_params(_params())
    with pytest.raises(ValueError, match="expected_updated does not match"):
        _run(handler.validate_against_upstream(params, _user()))


def test_upstream_tolerates_equivalent_timestamp_formats(monkeypatch):
    _fake_calendar_api(monkeypatch, event=_live_event(updated="2026-03-10T18:22:41.512Z"))
    handler = _handler()
    params = handler.validate_params(_params(expected_updated="2026-03-10T18:22:41.512+00:00"))
    out = _run(handler.validate_against_upstream(params, _user()))
    assert "current_event" in out


def test_upstream_rejects_missing_and_cancelled_events(monkeypatch):
    handler = _handler()
    _fake_calendar_api(monkeypatch, get_status=404)
    with pytest.raises(ValueError, match="Event not found"):
        _run(handler.validate_against_upstream(handler.validate_params(_params()), _user()))
    _fake_calendar_api(monkeypatch, event=_live_event(status="cancelled"))
    with pytest.raises(ValueError, match="cancelled"):
        _run(handler.validate_against_upstream(handler.validate_params(_params()), _user()))


def test_upstream_rejects_noop_edit(monkeypatch):
    _fake_calendar_api(monkeypatch)
    handler = _handler()
    # Same instant, different offset spelling; same attendees, different order.
    params = handler.validate_params({
        "event_id": "abc123",
        "expected_updated": UPDATED,
        "start": "2026-03-18T23:00:00Z",
        "attendees": ["Bob@example.com", "alice@example.com"],
        "summary": "Team sync",
    })
    with pytest.raises(ValueError, match="No changes"):
        _run(handler.validate_against_upstream(params, _user()))


def test_upstream_checks_merged_start_end_order(monkeypatch):
    _fake_calendar_api(monkeypatch)
    handler = _handler()
    # Only start supplied, after the live end.
    params = handler.validate_params({"event_id": "abc123", "expected_updated": UPDATED,
                                      "start": "2026-03-18T18:00:00-07:00"})
    with pytest.raises(ValueError, match="end must be after start"):
        _run(handler.validate_against_upstream(params, _user()))
    # Only start supplied as all-day against a timed live end.
    params = handler.validate_params({"event_id": "abc123", "expected_updated": UPDATED,
                                      "start": "2026-03-18"})
    with pytest.raises(ValueError, match="supply both start and end"):
        _run(handler.validate_against_upstream(params, _user()))


def test_upstream_defers_on_transient_failure(monkeypatch):
    handler = _handler()
    params = handler.validate_params(_params())
    _fake_calendar_api(monkeypatch, get_status=500)
    assert _run(handler.validate_against_upstream(params, _user())) == params
    _fake_calendar_api(monkeypatch, raise_on_get=RuntimeError("network"))
    assert _run(handler.validate_against_upstream(params, _user())) == params
    # Not connected: leave the authoritative error to execute().
    assert _run(handler.validate_against_upstream(params, _user(connected=False))) == params


def test_enrich_resolves_calendar_name(monkeypatch):
    _fake_calendar_api(monkeypatch)
    handler = _handler()
    params = handler.validate_params(_params())
    _run(handler.enrich_params_for_preview(params, _user()))
    assert params["calendar_name"] == "Work"


# ---------------------------------------------------------------------------
# render_preview
# ---------------------------------------------------------------------------


def _preview_map(fields):
    return {f["key"]: f for f in fields}


def test_preview_shows_before_after_and_scope(monkeypatch):
    _fake_calendar_api(monkeypatch, event=_live_event(recurrence=["RRULE:FREQ=WEEKLY"]))
    handler = _handler()
    params = handler.validate_params(_params(summary="Team sync (moved)", location="",
                                             attendees=["alice@example.com", "carol@example.com"]))
    params = _run(handler.validate_against_upstream(params, _user()))
    params["calendar_name"] = "Work"
    fields = _preview_map(_run(handler.render_preview(params)))
    assert fields["Event"]["value"] == "Team sync"
    assert fields["Calendar"]["value"] == "Work"
    assert fields["Scope"]["value"] == "Entire recurring series"
    assert fields["Title"]["value"] == "Team sync  →  Team sync (moved)"
    assert fields["Start"]["value"] == "Mar 18, 2026, 4:00 PM PDT  →  Mar 18, 2026, 5:00 PM PDT"
    assert fields["End"]["value"].endswith("5:30 PM PDT")
    assert fields["Location"]["value"] == "Zoom  →  (empty)"
    assert fields["Attendees"]["value"] == "add carol@example.com; remove bob@example.com"
    assert fields["Notifications"]["value"].startswith("Attendees will be emailed")
    assert "Description" not in fields


def test_preview_description_diff_and_instance_scope(monkeypatch):
    _fake_calendar_api(monkeypatch, event=_live_event(recurringEventId="master1"))
    handler = _handler()
    params = handler.validate_params({"event_id": "abc123", "expected_updated": UPDATED,
                                      "description": "Weekly sync\nAgenda: hiring"})
    params = _run(handler.validate_against_upstream(params, _user()))
    fields = _preview_map(_run(handler.render_preview(params)))
    assert fields["Scope"]["value"] == "This occurrence only"
    desc = fields["Description"]
    assert desc["type"] == "skill_content_diff"
    assert desc["diff"]["added"] == 1 and desc["diff"]["removed"] == 1
    assert [l["type"] for l in desc["diff"]["lines"]] == ["context", "del", "add"]


def test_preview_without_snapshot_shows_new_values_only():
    handler = _handler()
    params = handler.validate_params(_params(summary="Renamed", start="2026-03-18", end="2026-03-20",
                                             description="plain"))
    fields = _preview_map(_run(handler.render_preview(params)))
    assert fields["Event"]["value"] == "abc123"
    assert fields["Title"]["value"] == "Renamed"
    assert fields["Start"]["value"] == "Mar 18, 2026 (all day)"
    assert fields["End"]["value"] == "Mar 19, 2026 (all day)"  # exclusive end shown inclusive
    assert fields["Description"] == {"key": "Description", "value": "plain"}
    assert "Scope" not in fields and "Notifications" not in fields


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def test_execute_requires_connection_and_scope(monkeypatch):
    _fake_calendar_api(monkeypatch)
    handler = _handler()
    params = handler.validate_params(_params())
    with pytest.raises(RuntimeError, match="not connected"):
        _run(handler.execute(params, _user(connected=False)))
    with pytest.raises(RuntimeError, match="reconnecting Google Services"):
        _run(handler.execute(params, _user(scopes=("https://www.googleapis.com/auth/calendar.readonly",))))


def test_execute_reverifies_updated_and_existence(monkeypatch):
    handler = _handler()
    params = handler.validate_params(_params())
    captured = []
    _fake_calendar_api(monkeypatch, event=_live_event(updated="2026-03-11T09:00:00Z"), captured=captured)
    with pytest.raises(RuntimeError, match="changed since this request was created"):
        _run(handler.execute(params, _user()))
    assert [c["method"] for c in captured] == ["GET"]  # no PATCH sent
    _fake_calendar_api(monkeypatch, get_status=404)
    with pytest.raises(RuntimeError, match="no longer exists"):
        _run(handler.execute(params, _user()))
    _fake_calendar_api(monkeypatch, event=_live_event(status="cancelled"))
    with pytest.raises(RuntimeError, match="deleted"):
        _run(handler.execute(params, _user()))


def test_execute_patches_only_supplied_fields_and_preserves_attendee_records(monkeypatch):
    captured = []
    _fake_calendar_api(monkeypatch, captured=captured)
    handler = _handler()
    params = handler.validate_params(_params(
        attendees=["bob@example.com", "carol@example.com"], location="Room 4",
    ))
    result = _run(handler.execute(params, _user()))

    patch = [c for c in captured if c["method"] == "PATCH"]
    assert len(patch) == 1
    assert patch[0]["url"].endswith("/calendars/primary/events/abc123")
    body = patch[0]["kwargs"]["json"]
    assert set(body) == {"start", "end", "attendees", "location"}
    assert body["start"] == {"dateTime": "2026-03-18T17:00:00-07:00", "date": None,
                             "timeZone": "America/Los_Angeles"}
    assert body["end"]["dateTime"] == "2026-03-18T17:30:00-07:00"
    # Bob keeps his live record (optional flag, responseStatus); Carol is new.
    assert body["attendees"] == [
        {"email": "bob@example.com", "responseStatus": "needsAction", "optional": True},
        {"email": "carol@example.com"},
    ]
    assert patch[0]["kwargs"]["params"] == {"sendUpdates": "all"}
    assert result["success"] is True
    assert result["event_id"] == "abc123"
    assert result["updated_fields"] == ["start", "end", "attendees", "location"]
    assert result["html_link"].startswith("https://calendar.google.com/")


def test_execute_no_send_updates_without_attendees(monkeypatch):
    captured = []
    _fake_calendar_api(monkeypatch, event=_live_event(attendees=[]), captured=captured)
    handler = _handler()
    params = handler.validate_params({"event_id": "abc123", "expected_updated": UPDATED,
                                      "summary": "Renamed"})
    _run(handler.execute(params, _user()))
    patch = [c for c in captured if c["method"] == "PATCH"][0]
    assert patch["kwargs"]["json"] == {"summary": "Renamed"}
    assert patch["kwargs"]["params"] is None


def test_execute_switch_to_all_day_clears_datetime(monkeypatch):
    captured = []
    _fake_calendar_api(monkeypatch, captured=captured)
    handler = _handler()
    params = handler.validate_params(_params(start="2026-03-18", end="2026-03-19"))
    _run(handler.execute(params, _user()))
    body = [c for c in captured if c["method"] == "PATCH"][0]["kwargs"]["json"]
    assert body["start"] == {"date": "2026-03-18", "dateTime": None, "timeZone": None}
    assert body["end"] == {"date": "2026-03-19", "dateTime": None, "timeZone": None}


def test_execute_time_zone_only_rewrites_bounds_from_live_event(monkeypatch):
    captured = []
    _fake_calendar_api(monkeypatch, captured=captured)
    handler = _handler()
    params = handler.validate_params({"event_id": "abc123", "expected_updated": UPDATED,
                                      "time_zone": "Europe/London"})
    _run(handler.execute(params, _user()))
    body = [c for c in captured if c["method"] == "PATCH"][0]["kwargs"]["json"]
    assert body["start"] == {"dateTime": "2026-03-18T16:00:00-07:00", "date": None,
                             "timeZone": "Europe/London"}
    assert body["end"]["timeZone"] == "Europe/London"


def test_execute_surfaces_api_errors(monkeypatch):
    _fake_calendar_api(monkeypatch, patch_status=400)
    handler = _handler()
    params = handler.validate_params(_params())
    with pytest.raises(RuntimeError, match="Google Calendar API error \\(updating the event\\): boom"):
        _run(handler.execute(params, _user()))
