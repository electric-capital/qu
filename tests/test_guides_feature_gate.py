"""Tests for the ``guides`` feature gate.

Guides are deprecated in favor of skills; the whole feature sits behind the
admin ``guides`` gate in config/feature_gates.py (off by default, per-user
capable) so an existing install can keep it alive while users migrate.
Covers the registry entry, the /guides route lockdown (403
``guides_disabled``), the routine guide-override guard, and the
custom_system_prompt -> default-guide settings sync being skipped while
the gate is closed.

All DB calls are monkeypatched -- no database is touched.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import config.feature_gates as fg
from chat.guide_routes import (
    convert_guide_to_skill,
    create_user_guide,
    delete_user_guide,
    get_user_guide,
    list_user_guides,
    update_user_guide,
    UpdateGuideRequest,
)
from chat.routine_routes import (
    CreateRoutineRequest,
    UpdateRoutineRequest,
    create_project_routine,
    update_project_routine,
)


def _run(coro):
    return asyncio.run(coro)


_USER = {"id": 1, "email": "user@example.com"}
_OTHER = {"id": 2, "email": "other@example.com"}

_GUIDE = {
    "id": "guide-1",
    "user_id": 1,
    "name": "Investing",
    "content": "Always cite sources.",
    "is_default": False,
}

_ROUTINE = {
    "id": "routine-1",
    "project_id": "proj-1",
    "user_id": 1,
    "name": "Daily",
    "prompt": "Do the thing",
    "guide_id": None,
    "model": None,
    "updated_at": "2026-01-01T00:00:00",
}


@pytest.fixture
def gates_file(tmp_path, monkeypatch):
    """Point the store at a per-test file (missing initially = all off)."""
    path = tmp_path / "feature_gates.json"
    monkeypatch.setattr(fg, "FEATURE_GATES_FILE", path)
    return path


def _expect_disabled(coro):
    with pytest.raises(HTTPException) as exc_info:
        _run(coro)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail["error"] == "guides_disabled"


class TestRegistry:
    def test_registered_off_by_default_and_per_user(self, gates_file):
        assert fg.FEATURE_GUIDES in fg.KNOWN_FEATURES
        assert fg.FEATURE_GUIDES in fg.PER_USER_ACCESS_FEATURES
        assert fg.FEATURE_GUIDES in fg.FEATURE_LABELS
        assert not fg.is_feature_enabled(fg.FEATURE_GUIDES)
        assert not fg.guides_enabled_for("user@example.com")
        assert fg.FEATURE_GUIDES not in fg.enabled_features("user@example.com")

    def test_guides_enabled_for_honors_allowed_users(self, gates_file):
        fg.set_feature_enabled(fg.FEATURE_GUIDES, True)
        assert fg.guides_enabled_for("anyone@example.com")

        fg.set_feature_allowed_users(fg.FEATURE_GUIDES, ["User@Example.com"])
        assert fg.guides_enabled_for("user@example.com")
        assert not fg.guides_enabled_for("other@example.com")
        assert fg.FEATURE_GUIDES in fg.enabled_features("user@example.com")
        assert fg.FEATURE_GUIDES not in fg.enabled_features("other@example.com")


class TestGuideRoutes:
    def test_every_route_rejects_while_closed(self, gates_file):
        _expect_disabled(list_user_guides(user=_USER))
        _expect_disabled(create_user_guide(user=_USER))
        _expect_disabled(get_user_guide("guide-1", user=_USER))
        _expect_disabled(
            update_user_guide("guide-1", UpdateGuideRequest(name="x"), user=_USER)
        )
        _expect_disabled(delete_user_guide("guide-1", user=_USER))
        _expect_disabled(convert_guide_to_skill("guide-1", user=_USER))

    def test_routes_work_while_open(self, gates_file):
        fg.set_feature_enabled(fg.FEATURE_GUIDES, True)
        with patch("chat.guide_routes.list_guides", AsyncMock(return_value=[_GUIDE])):
            assert _run(list_user_guides(user=_USER)) == {"guides": [_GUIDE]}
        with patch("chat.guide_routes.get_guide", AsyncMock(return_value=_GUIDE)):
            assert _run(get_user_guide("guide-1", user=_USER)) == _GUIDE
        # Creation stays retired (410) even with the gate open.
        with pytest.raises(HTTPException) as exc_info:
            _run(create_user_guide(user=_USER))
        assert exc_info.value.status_code == 410

    def test_allowed_users_list_is_enforced_per_user(self, gates_file):
        fg.set_feature_enabled(fg.FEATURE_GUIDES, True)
        fg.set_feature_allowed_users(fg.FEATURE_GUIDES, [_USER["email"]])
        with patch("chat.guide_routes.list_guides", AsyncMock(return_value=[])):
            assert _run(list_user_guides(user=_USER)) == {"guides": []}
        _expect_disabled(list_user_guides(user=_OTHER))


class TestRoutineGuideOverride:
    def test_create_with_guide_rejected_while_closed(self, gates_file):
        create = AsyncMock(return_value=_ROUTINE)
        with patch("chat.routine_routes.get_project", AsyncMock(return_value={"id": "proj-1"})), \
             patch("chat.routine_routes.create_routine", create):
            _expect_disabled(create_project_routine(
                "proj-1",
                CreateRoutineRequest(name="Daily", prompt="Do it", guide_id="guide-1"),
                user=_USER,
            ))
        create.assert_not_awaited()

    def test_create_without_guide_allowed_while_closed(self, gates_file):
        create = AsyncMock(return_value=_ROUTINE)
        with patch("chat.routine_routes.get_project", AsyncMock(return_value={"id": "proj-1"})), \
             patch("chat.routine_routes.create_routine", create):
            result = _run(create_project_routine(
                "proj-1",
                CreateRoutineRequest(name="Daily", prompt="Do it"),
                user=_USER,
            ))
        assert result["id"] == "routine-1"
        create.assert_awaited_once()

    def test_create_with_guide_allowed_while_open(self, gates_file):
        fg.set_feature_enabled(fg.FEATURE_GUIDES, True)
        create = AsyncMock(return_value={**_ROUTINE, "guide_id": "guide-1"})
        with patch("chat.routine_routes.get_project", AsyncMock(return_value={"id": "proj-1"})), \
             patch("chat.routine_routes.create_routine", create):
            _run(create_project_routine(
                "proj-1",
                CreateRoutineRequest(name="Daily", prompt="Do it", guide_id="guide-1"),
                user=_USER,
            ))
        assert create.await_args.kwargs["guide_id"] == "guide-1"

    def test_update_attach_rejected_but_clear_allowed_while_closed(self, gates_file):
        update = AsyncMock(return_value=_ROUTINE)
        with patch("chat.routine_routes.get_project", AsyncMock(return_value={"id": "proj-1"})), \
             patch("chat.routine_routes.update_routine", update):
            _expect_disabled(update_project_routine(
                "proj-1", "routine-1",
                UpdateRoutineRequest(guide_id="guide-1"),
                user=_USER,
            ))
            update.assert_not_awaited()

            _run(update_project_routine(
                "proj-1", "routine-1",
                UpdateRoutineRequest(clear_guide=True),
                user=_USER,
            ))
        assert update.await_args.kwargs["guide_id"] is None


class TestSettingsSync:
    def _call(self, prompt="Be terse."):
        from chat.routes.user import UserSettingsUpdate, update_settings
        return update_settings(UserSettingsUpdate(custom_system_prompt=prompt), user=_USER)

    def test_default_guide_sync_skipped_while_closed(self, gates_file):
        get_default = AsyncMock(return_value={"id": "guide-default"})
        update_guide = AsyncMock()
        with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
             patch("db.user_store.update_user_settings", AsyncMock(return_value={"settings": {}})), \
             patch("db.guide_store.get_default_guide", get_default), \
             patch("db.guide_store.update_guide", update_guide), \
             patch("chat.gemini_api.invalidate_user_sessions"):
            _run(self._call())
        get_default.assert_not_awaited()
        update_guide.assert_not_awaited()

    def test_default_guide_sync_runs_while_open(self, gates_file):
        fg.set_feature_enabled(fg.FEATURE_GUIDES, True)
        get_default = AsyncMock(return_value={"id": "guide-default"})
        update_guide = AsyncMock()
        with patch("db.user_store.get_user_by_email", AsyncMock(return_value=_USER)), \
             patch("db.user_store.update_user_settings", AsyncMock(return_value={"settings": {}})), \
             patch("db.guide_store.get_default_guide", get_default), \
             patch("db.guide_store.update_guide", update_guide), \
             patch("chat.gemini_api.invalidate_user_sessions"):
            _run(self._call())
        update_guide.assert_awaited_once_with(1, "guide-default", content="Be terse.")
