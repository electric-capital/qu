"""Pre-card checks + preview enrichment for skill create/edit action requests.

Runs in the ``create_action_request`` dispatch arm (after
``validate_against_upstream``, before the request row is written) -- the same
async, ``user``-and-``project_id``-bearing place where upstream dry-run
resolution and preview-name injection already run. It performs:

- **Name-collision pre-check (D5):** for ``create_skill`` (when ``name`` is
  set) and ``edit_skill`` (only when ``name`` is changing), scoped per the
  resolved target (per-creator for user skills, per-project for project
  skills, excluding the edited skill's own id). On a hit it raises
  ``ValueError`` so the dispatch arm surfaces ``Invalid parameters`` before
  any card.
- **Project-access pre-check (D2):** when the resolved target is a project
  skill, verifies the turn's ``project_id`` is set, matches, and that the user
  owns the project.
- **project_autoload mismatch pre-check (D8):** rejects ``project_autoload`` on
  ``edit_skill`` when the target is a user skill.
- **Content-edit resolution:** for ``edit_skill`` content edits
  (``old_string`` / ``new_string``), enforces the read-before-edit gate (the
  conversation must have loaded or read the skill, per
  ``ChatStorage.get_skill_read_ids``) and applies the replacement against
  the current content so a failed / ambiguous match is rejected same-turn.
- **Preview enrichment:** injects ``current_skill_name`` / ``skill_target``
  and (for content edits) the ``content_diff`` line diff for ``edit_skill``
  (server-injected, NOT in ``_ALLOWED_PARAMS``, parallel to
  ``calendar_name``) so the card header and diff snippet read nicely.

These are an agent-experience optimization; the execute-time guards in the
handlers remain the authoritative enforcement (TOCTOU close).
"""

from chat.action_request_types._skill_content_edit import (
    apply_content_edit,
    build_content_diff,
)
from chat.storage import ChatStorage
from db import skill_store, project_store


async def skill_precard_check(
    req_type: str,
    validated_params: dict,
    user: dict,
    project_id: str | None,
    conversation_id: str | None = None,
) -> None:
    """Run pre-card checks, mutating ``validated_params`` for enrichment.

    Raises:
        ValueError: On a name collision, project-access failure, a
            project_autoload/user-skill mismatch, an unread skill, or a
            failed content-edit match -- surfaced as ``Invalid parameters``
            by the dispatch arm.
    """
    if req_type == "create_skill":
        await _create_precard(validated_params, user, project_id)
    elif req_type == "edit_skill":
        await _edit_precard(validated_params, user, project_id, conversation_id)


async def _create_precard(
    validated_params: dict, user: dict, project_id: str | None
) -> None:
    target = validated_params.get("target", "user")
    name = validated_params.get("name")

    if target == "project":
        project = (
            await project_store.get_project(user["id"], project_id)
            if project_id else None
        )
        if project is None:
            raise ValueError(
                "This conversation is not in a project you own, so a project "
                "skill cannot be created here."
            )
        if project.get("public"):
            raise ValueError(
                "Public projects cannot have project skills."
            )
        # Per-project name collision.
        existing = await skill_store.list_project_skills(project_id)
        if any(s["name"] == name for s in existing):
            raise ValueError(
                f"A skill named '{name}' already exists in this project."
            )
        return

    # user target: per-creator name collision.
    owned = await skill_store.list_accessible_skills(user["id"], owned_only=True)
    if any(s["name"] == name for s in owned):
        raise ValueError(f"A skill named '{name}' already exists for you.")


async def _edit_precard(
    validated_params: dict,
    user: dict,
    project_id: str | None,
    conversation_id: str | None,
) -> None:
    skill_id = validated_params["skill_id"]
    skill = await skill_store.get_skill(skill_id)
    if not skill:
        raise ValueError("Skill not found.")

    is_project_skill = bool(skill.get("project_id"))

    # Preview enrichment (server-injected; not in _ALLOWED_PARAMS).
    validated_params["current_skill_name"] = skill.get("name")
    validated_params["skill_target"] = "project" if is_project_skill else "user"

    # Content search-and-replace: read-before-edit gate, then resolve the
    # replacement against the CURRENT content so a failed / ambiguous match
    # is rejected same-turn (no card), and inject the line diff the card
    # renders. Execute re-applies the match against the then-live content.
    if "old_string" in validated_params:
        read_ids = (
            ChatStorage.get_skill_read_ids(conversation_id)
            if conversation_id
            else []
        )
        if skill_id not in read_ids:
            raise ValueError(
                f"Skill '{skill.get('name')}' has not been loaded or read "
                "in this conversation. Read it with get_skill (or load it "
                "with load_skills) before editing its content."
            )
        current_content = skill.get("content") or ""
        new_content, _replacements = apply_content_edit(
            current_content,
            validated_params["old_string"],
            validated_params["new_string"],
            bool(validated_params.get("replace_all")),
        )
        validated_params["content_diff"] = build_content_diff(
            current_content, new_content
        )

    if is_project_skill:
        # Project access + match gate.
        skill_project_id = skill["project_id"]
        skill_project = await project_store.get_project(
            user["id"], skill_project_id
        )
        if not project_id or project_id != skill_project_id or skill_project is None:
            raise ValueError("You do not have access to this project skill.")
        # Belt and braces: public projects cannot have project skills, so
        # there should be nothing to edit here.
        if skill_project.get("public"):
            raise ValueError("Public projects cannot have project skills.")
        if any(
            k in validated_params
            for k in ("visibility", "add_share_emails", "remove_share_emails")
        ):
            raise ValueError(
                "visibility and share changes are not allowed for project skills."
            )
        # Per-project name collision when name changes.
        new_name = validated_params.get("name")
        if new_name is not None and new_name != skill["name"]:
            existing = await skill_store.list_project_skills(skill_project_id)
            if any(
                s["name"] == new_name and s["id"] != skill_id for s in existing
            ):
                raise ValueError(
                    f"A skill named '{new_name}' already exists in this project."
                )
        return

    # user skill
    if "project_autoload" in validated_params:
        raise ValueError(
            "auto-load toggling is only available for project skills."
        )
    # Per-creator name collision when name changes.
    new_name = validated_params.get("name")
    if new_name is not None and new_name != skill["name"]:
        owned = await skill_store.list_accessible_skills(
            user["id"], owned_only=True
        )
        if any(s["name"] == new_name and s["id"] != skill_id for s in owned):
            raise ValueError(f"A skill named '{new_name}' already exists for you.")
