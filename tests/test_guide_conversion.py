"""Tests for the guide-to-skill conversion endpoint and guide-creation lockdown.

Guides are deprecated in favor of skills. ``POST /guides/{id}/convert-to-skill``
creates a private skill from the guide's content and deletes the guide;
converting the default guide additionally enables user-level auto-load on the
new skill. ``POST /guides`` is disabled and returns 410.

All DB calls are monkeypatched -- no database is touched.
"""

import asyncio
from unittest.mock import patch, AsyncMock

import pytest
from fastapi import HTTPException

from chat.guide_routes import convert_guide_to_skill, create_user_guide


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _guides_gate_open():
    """These tests exercise the routes' behavior with the guides feature gate
    open; the closed-gate 403 is covered in test_guides_feature_gate.py."""
    with patch("chat.guide_routes.guides_enabled_for", return_value=True):
        yield


_USER = {"id": 1, "email": "user@example.com"}

_GUIDE = {
    "id": "guide-1",
    "user_id": 1,
    "name": "Investing",
    "content": "Always cite sources.",
    "is_default": False,
}

_DEFAULT_GUIDE = {
    "id": "guide-default",
    "user_id": 1,
    "name": "Default",
    "content": "Be concise.",
    "is_default": True,
}


def _skill_for(name):
    return {
        "id": "skill-1",
        "creator_id": 1,
        "name": name,
        "description": f"Converted from a guide.",
        "content": "whatever",
        "visibility": "private",
    }


def test_convert_regular_guide_creates_skill_and_deletes_guide():
    create_skill = AsyncMock(side_effect=lambda **kw: _skill_for(kw["name"]))
    autoload = AsyncMock()
    delete = AsyncMock(return_value=True)

    with patch("chat.guide_routes.get_guide", AsyncMock(return_value=dict(_GUIDE))), \
         patch("chat.guide_routes.create_skill", create_skill), \
         patch("chat.guide_routes.set_user_skill_autoload", autoload), \
         patch("chat.guide_routes.delete_guide", delete):
        result = _run(convert_guide_to_skill("guide-1", user=_USER))

    create_skill.assert_awaited_once_with(
        creator_id=1,
        name="Investing",
        description="Converted from the 'Investing' guide.",
        content="Always cite sources.",
        visibility="private",
    )
    autoload.assert_not_awaited()
    delete.assert_awaited_once_with(1, "guide-1")
    assert result["autoload_enabled"] is False
    assert result["skill"]["name"] == "Investing"


def test_convert_default_guide_enables_autoload():
    create_skill = AsyncMock(side_effect=lambda **kw: _skill_for(kw["name"]))
    autoload = AsyncMock()
    delete = AsyncMock(return_value=True)

    with patch("chat.guide_routes.get_guide", AsyncMock(return_value=dict(_DEFAULT_GUIDE))), \
         patch("chat.guide_routes.create_skill", create_skill), \
         patch("chat.guide_routes.set_user_skill_autoload", autoload), \
         patch("chat.guide_routes.delete_guide", delete):
        result = _run(convert_guide_to_skill("guide-default", user=_USER))

    autoload.assert_awaited_once_with(1, "skill-1", True)
    delete.assert_awaited_once_with(1, "guide-default")
    assert result["autoload_enabled"] is True


def test_convert_missing_guide_404():
    with patch("chat.guide_routes.get_guide", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            _run(convert_guide_to_skill("nope", user=_USER))
    assert exc_info.value.status_code == 404


def test_convert_empty_guide_400_and_nothing_mutated():
    empty_guide = dict(_GUIDE, content="   \n")
    create_skill = AsyncMock()
    delete = AsyncMock()

    with patch("chat.guide_routes.get_guide", AsyncMock(return_value=empty_guide)), \
         patch("chat.guide_routes.create_skill", create_skill), \
         patch("chat.guide_routes.delete_guide", delete):
        with pytest.raises(HTTPException) as exc_info:
            _run(convert_guide_to_skill("guide-1", user=_USER))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "empty_guide"
    create_skill.assert_not_awaited()
    delete.assert_not_awaited()


def test_convert_name_collision_retries_with_suffix():
    calls = []

    async def _create(**kwargs):
        calls.append(kwargs["name"])
        if len(calls) == 1:
            raise Exception("UNIQUE constraint failed: skills.creator_id, skills.name")
        return _skill_for(kwargs["name"])

    with patch("chat.guide_routes.get_guide", AsyncMock(return_value=dict(_GUIDE))), \
         patch("chat.guide_routes.create_skill", _create), \
         patch("chat.guide_routes.set_user_skill_autoload", AsyncMock()), \
         patch("chat.guide_routes.delete_guide", AsyncMock(return_value=True)):
        result = _run(convert_guide_to_skill("guide-1", user=_USER))

    assert calls == ["Investing", "Investing (converted)"]
    assert result["skill"]["name"] == "Investing (converted)"


def test_convert_persistent_collision_409_and_guide_kept():
    create_skill = AsyncMock(
        side_effect=Exception("UNIQUE constraint failed: skills.creator_id, skills.name")
    )
    delete = AsyncMock()

    with patch("chat.guide_routes.get_guide", AsyncMock(return_value=dict(_GUIDE))), \
         patch("chat.guide_routes.create_skill", create_skill), \
         patch("chat.guide_routes.delete_guide", delete):
        with pytest.raises(HTTPException) as exc_info:
            _run(convert_guide_to_skill("guide-1", user=_USER))

    assert exc_info.value.status_code == 409
    delete.assert_not_awaited()


def test_convert_validation_error_400():
    create_skill = AsyncMock(side_effect=ValueError("Skill content cannot be empty."))

    with patch("chat.guide_routes.get_guide", AsyncMock(return_value=dict(_GUIDE))), \
         patch("chat.guide_routes.create_skill", create_skill):
        with pytest.raises(HTTPException) as exc_info:
            _run(convert_guide_to_skill("guide-1", user=_USER))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["error"] == "validation_error"


def test_create_guide_is_gone():
    with pytest.raises(HTTPException) as exc_info:
        _run(create_user_guide(user=_USER))

    assert exc_info.value.status_code == 410
    assert exc_info.value.detail["error"] == "guides_deprecated"
