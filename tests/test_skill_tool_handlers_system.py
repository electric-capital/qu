"""Tests for the skill tool handlers' system-skill integration.

Verifies that ``_handle_list_skills``, ``_handle_search_skills``, and
``_handle_load_skills`` correctly merge hardcoded system skills with the
DB-backed skills they used to return exclusively.
"""

import asyncio
import json
from unittest.mock import patch

from chat.gemini_api.tool_handlers import (
    _handle_list_skills,
    _handle_load_skills,
    _handle_search_skills,
)


ALL_CONNECTED = {
    "google_services": True,
    "slack": True,
    "telegram": True,
    "example": True,  # the _example plugin's skill gate
    "airtable": True,
    "github": True,
    "twitter": True,
}


def _run(coro):
    return asyncio.run(coro)


def _fake_async(value):
    """Return an async function that ignores its args and resolves to value."""
    async def _inner(*args, **kwargs):
        return value
    return _inner


# ---------------------------------------------------------------------------
# list_skills
# ---------------------------------------------------------------------------

def test_list_skills_includes_system_entries_first():
    fake_db_skills = [
        {
            "id": "uuid-1",
            "name": "My Skill",
            "description": "user-defined",
            "visibility": "private",
        },
    ]
    with patch("db.skill_store.list_accessible_skills", new=_fake_async(fake_db_skills)):
        result = _run(_handle_list_skills(
            user_id=1,
            connected_services=ALL_CONNECTED,
            has_project=True,
        ))
    payload = json.loads(result)
    ids = [s["id"] for s in payload["skills"]]
    # System skills come first.
    assert ids[0].startswith("system:")
    # The DB skill is preserved at the tail.
    assert ids[-1] == "uuid-1"
    # System skill entries are tagged.
    system_entries = [s for s in payload["skills"] if s["id"].startswith("system:")]
    assert all(s["visibility"] == "system" for s in system_entries)
    assert all("when_to_load" in s for s in system_entries)


def test_list_skills_excludes_disconnected_backends(telegram_plugin):
    services = dict(ALL_CONNECTED)
    services["slack"] = False
    services["telegram"] = False
    with patch("db.skill_store.list_accessible_skills", new=_fake_async([])):
        result = _run(_handle_list_skills(
            user_id=1, connected_services=services, has_project=False,
        ))
    payload = json.loads(result)
    ids = {s["id"] for s in payload["skills"]}
    assert "system:slack" not in ids
    assert "system:telegram" not in ids
    assert "system:gmail" in ids


# ---------------------------------------------------------------------------
# search_skills
# ---------------------------------------------------------------------------

def test_search_skills_matches_system_by_keyword():
    with patch("db.skill_store.search_accessible_skills", new=_fake_async([])):
        result = _run(_handle_search_skills(
            user_id=1, keyword="email",
            connected_services=ALL_CONNECTED, has_project=False,
        ))
    payload = json.loads(result)
    ids = {s["id"] for s in payload["skills"]}
    # "Load when the user asks about email..." matches `system:gmail`.
    assert "system:gmail" in ids


def test_search_skills_rejects_empty_keyword():
    result = _run(_handle_search_skills(
        user_id=1, keyword="   ",
        connected_services=ALL_CONNECTED, has_project=False,
    ))
    payload = json.loads(result)
    assert "error" in payload


# ---------------------------------------------------------------------------
# load_skills
# ---------------------------------------------------------------------------

def test_load_skills_handles_system_and_db_ids_together():
    fake_db_skill = {
        "id": "uuid-1",
        "name": "User Skill",
        "description": "user-defined",
        "content": "user content",
    }

    async def fake_get(user_id, ids):
        # The DB call should ONLY receive non-system ids.
        assert "system:memory" not in ids
        return [fake_db_skill]

    with patch("db.skill_store.get_accessible_skills_by_ids", new=fake_get):
        result = _run(_handle_load_skills(
            user_id=1,
            skill_ids=["system:memory", "uuid-1"],
            connected_services=ALL_CONNECTED,
            has_project=False,
            base_url="http://localhost:8000",
            api_key="test",
        ))
    # Result is a markdown document, not JSON.
    assert result.startswith("# Loaded skills")
    assert "Loaded 2 skill(s). 0 error(s)." in result
    # System skill metadata header appears with its id.
    assert "**ID:** `system:memory`" in result
    assert "**Visibility:** system" in result
    # DB skill's id appears too.
    assert "**ID:** `uuid-1`" in result
    # Both skills have content sections.
    assert result.count("## Skill Content") == 2
    # Order: system skill first, DB skill second.
    system_id_idx = result.index("**ID:** `system:memory`")
    db_id_idx = result.index("**ID:** `uuid-1`")
    assert system_id_idx < db_id_idx
    # User skill heading and content appear verbatim.
    assert "# User Skill" in result
    assert "user content" in result


def test_load_skills_silently_skips_unknown_system_ids():
    async def fake_get(user_id, ids):
        assert ids == []  # No DB ids.
        return []

    with patch("db.skill_store.get_accessible_skills_by_ids", new=fake_get):
        result = _run(_handle_load_skills(
            user_id=1,
            skill_ids=["system:does-not-exist"],
            connected_services=ALL_CONNECTED,
            has_project=False,
            base_url="http://localhost:8000",
            api_key="test",
        ))
    assert result.startswith("# Loaded skills")
    assert "Loaded 0 skill(s). 0 error(s)." in result
    # Empty-result sentinel present.
    assert "_No skills matched the requested ids." in result
    # No entries means no separators and no content blocks.
    assert "===" not in result
    assert "## Skill Content" not in result


def test_load_skills_returns_gate_error_for_disconnected_backend(example_plugin):
    services = dict(ALL_CONNECTED)
    services["example"] = False

    async def fake_get(user_id, ids):
        return []

    with patch("db.skill_store.get_accessible_skills_by_ids", new=fake_get):
        result = _run(_handle_load_skills(
            user_id=1,
            skill_ids=["system:example"],
            connected_services=services,
            has_project=False,
            base_url="http://localhost:8000",
            api_key="test",
        ))
    assert result.startswith("# Loaded skills")
    assert "Loaded 0 skill(s). 1 error(s)." in result
    assert "## Load errors" in result
    # Error bullet references the skill id in a backtick span.
    assert "`system:example`" in result
    assert "not connected" in result.lower()
    # Gate-failed skills produce no Skill Content block and no === separator.
    assert "## Skill Content" not in result
    assert "===" not in result


def test_load_skills_rejects_empty_input():
    result = _run(_handle_load_skills(
        user_id=1,
        skill_ids=[],
        connected_services=ALL_CONNECTED,
        has_project=False,
        base_url="http://localhost:8000",
        api_key="test",
    ))
    # Empty-input remains a JSON error envelope (API-contract error).
    payload = json.loads(result)
    assert "error" in payload


def test_load_skills_renders_single_skill_markdown():
    fake_db_skill = {
        "id": "uuid-single",
        "name": "Solo Skill",
        "description": "solo description",
        "content": "solo content body",
    }

    async def fake_get(user_id, ids):
        return [fake_db_skill]

    with patch("db.skill_store.get_accessible_skills_by_ids", new=fake_get):
        result = _run(_handle_load_skills(
            user_id=1,
            skill_ids=["uuid-single"],
            connected_services=ALL_CONNECTED,
            has_project=False,
            base_url="http://localhost:8000",
            api_key="test",
        ))
    assert result.startswith("# Loaded skills")
    assert "Loaded 1 skill(s). 0 error(s)." in result
    # One === separator prefixing the single entry.
    assert result.count("\n===\n") == 1
    assert result.count("# Solo Skill") == 1
    assert result.count("## Skill Content") == 1
    # Content is emitted verbatim (un-fenced).
    assert "solo content body" in result
    # DB skills without a visibility field render as (unknown).
    assert "**Visibility:** (unknown)" in result


def test_load_skills_renders_errors_before_successes(example_plugin):
    services = dict(ALL_CONNECTED)
    services["example"] = False  # system:example will gate-fail

    fake_db_skill = {
        "id": "uuid-42",
        "name": "DB Skill",
        "description": "db desc",
        "content": "db content",
    }

    async def fake_get(user_id, ids):
        return [fake_db_skill]

    with patch("db.skill_store.get_accessible_skills_by_ids", new=fake_get):
        result = _run(_handle_load_skills(
            user_id=1,
            skill_ids=["system:example", "uuid-42"],
            connected_services=services,
            has_project=False,
            base_url="http://localhost:8000",
            api_key="test",
        ))
    assert "## Load errors" in result
    # The errors section must appear before the first === separator.
    errors_idx = result.index("## Load errors")
    sep_idx = result.index("\n===\n")
    assert errors_idx < sep_idx
    # And before any successful skill's Skill Content.
    content_idx = result.index("## Skill Content")
    assert errors_idx < content_idx
    # Counts reflect 1 success and 1 error.
    assert "Loaded 1 skill(s). 1 error(s)." in result


def test_load_skills_counts_match_payload(example_plugin):
    # Two system successes (memory, gmail), one DB success, one gate-failed system (example).
    services = dict(ALL_CONNECTED)
    services["example"] = False

    fake_db_skill = {
        "id": "uuid-99",
        "name": "Extra DB Skill",
        "description": "extra",
        "content": "extra content",
    }

    async def fake_get(user_id, ids):
        return [fake_db_skill]

    with patch("db.skill_store.get_accessible_skills_by_ids", new=fake_get):
        result = _run(_handle_load_skills(
            user_id=1,
            skill_ids=["system:memory", "system:gmail", "system:example", "uuid-99"],
            connected_services=services,
            has_project=False,
            base_url="http://localhost:8000",
            api_key="test",
        ))
    assert "Loaded 3 skill(s). 1 error(s)." in result
