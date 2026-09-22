"""EditSkillHandler -- approve-to-edit action request for DB-backed skills.

Partial-edit an existing skill identified by ``skill_id`` (the DB UUID the
model already gets from ``list_my_skills`` / ``get_skill``). The target (user
vs project skill) is inferred at execute time from the skill row's
``project_id`` -- there is no ``target`` param.

Content edits are a search-and-replace, mirroring ``edit_workspace_file``:
``old_string`` / ``new_string`` (+ optional ``replace_all``) instead of a
full-content overwrite. They are gated on the conversation having loaded or
read the skill (the ``skill_reads.json`` sidecar via
``ChatStorage.get_skill_read_ids``) and the replacement is applied against
the CURRENT content at both proposal time (pre-card check, which also
injects the ``content_diff`` preview) and approve time (TOCTOU close) --
see :mod:`chat.action_request_types._skill_content_edit`. Rows proposed
before this interface may still carry a legacy full ``content`` param;
``execute`` honors it, ``validate_params`` no longer accepts it.

User skills accept ``name`` / ``description`` / content edits /
``visibility`` plus ``add_share_emails`` / ``remove_share_emails`` (only
meaningful while the skill is ``shared``). Project skills accept only
``name`` / ``description`` / content edits plus the project-only
``project_autoload`` toggle. Restrictions that depend on the resolved target
(visibility/share rejection for project skills, ``project_autoload`` rejection
for user skills) are enforced in ``execute`` because ``validate_params`` has no
DB context.

Like ``create_skill`` (and ``create_memory``) there is no
``validate_against_upstream`` -- skills are an internal Quest store.
"""

import logging

from db.models import ActionRequestType
from db.skill_store import (
    MAX_SKILL_NAME_LENGTH,
    MAX_SKILL_DESCRIPTION_LENGTH,
    MAX_SKILL_CONTENT_SIZE,
)
from chat.action_request_types.base import ActionRequestHandler
from chat.action_request_types._param_validation import reject_unknown_params
from chat.action_request_types._skill_content_edit import apply_content_edit
from chat.system_skills.loader import is_system_skill_id

logger = logging.getLogger(__name__)


_ALLOWED_PARAMS = frozenset(
    {
        "skill_id",
        "name",
        "description",
        "old_string",
        "new_string",
        "replace_all",
        "visibility",
        "add_share_emails",
        "remove_share_emails",
        "project_autoload",
    }
)

_USER_VISIBILITIES = frozenset({"private", "shared", "public"})

# Fields that count as a meaningful edit. project_autoload counts (D8): a call
# carrying only skill_id + project_autoload is NOT an empty payload.
_EDITABLE_FIELDS = frozenset(
    {
        "name",
        "description",
        "old_string",
        "visibility",
        "add_share_emails",
        "remove_share_emails",
        "project_autoload",
    }
)


def _validate_email_list(value, key: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list of email strings.")
    normalized: list[str] = []
    for email in value:
        if not isinstance(email, str) or not email.strip():
            raise ValueError(f"{key} must be a list of non-empty email strings.")
        normalized.append(email.strip())
    return normalized


class EditSkillHandler(ActionRequestHandler):
    """Edit an existing DB-backed skill (partial update) after user approval."""

    @property
    def type_name(self) -> ActionRequestType:
        return ActionRequestType.EDIT_SKILL

    @property
    def display_name(self) -> str:
        return "Edit Skill"

    @property
    def approve_label(self) -> str:
        return "Save"

    @property
    def resolved_label(self) -> str:
        return "Saved"

    def summary_snippet(self, params: dict) -> str:
        # Prefer the server-injected current skill name; fall back to a
        # proposed name, then a short id.
        skill_id = params.get("skill_id")
        base = (str(params.get("current_skill_name") or "")
                or str(params.get("name") or "")
                or (f"Skill #{str(skill_id)[:8]}" if skill_id else ""))
        autoload = params.get("project_autoload")
        only_toggle = autoload is not None and all(
            params.get(key) is None
            for key in ("name", "description", "old_string", "content", "visibility")
        )
        if only_toggle:
            # For a toggle-only edit, suffix the auto-load action so the
            # summary is not a bare name.
            return f"{base} -- auto-load {'on' if autoload else 'off'}"
        return base

    def validate_params(self, params: dict) -> dict:
        # Friendlier than the generic unknown-param rejection for models
        # (or histories) still on the old full-content interface.
        if "content" in params:
            raise ValueError(
                "content full-replacement is no longer supported. Edit the "
                "skill content with an exact search-and-replace instead: "
                "old_string (exact current text, unique unless replace_all) "
                "and new_string."
            )
        reject_unknown_params(self.type_name.value, params, _ALLOWED_PARAMS)

        skill_id = params.get("skill_id")
        if skill_id is None:
            raise ValueError("Missing required parameter: skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("skill_id must be a non-empty string.")
        skill_id = skill_id.strip()
        if is_system_skill_id(skill_id):
            raise ValueError(
                "system:* skills are not editable (they are hardcoded, not "
                "DB-backed)."
            )

        out: dict = {"skill_id": skill_id}

        # name (required-if-present)
        if "name" in params and params["name"] is not None:
            name = params["name"]
            if not isinstance(name, str):
                name = str(name)
            name = name.strip()
            if not name:
                raise ValueError("Skill name cannot be empty.")
            if len(name) > MAX_SKILL_NAME_LENGTH:
                raise ValueError(
                    f"Skill name exceeds maximum length of {MAX_SKILL_NAME_LENGTH} characters."
                )
            out["name"] = name

        # description (optional)
        if "description" in params and params["description"] is not None:
            description = params["description"]
            if not isinstance(description, str):
                description = str(description)
            if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
                raise ValueError(
                    f"Skill description exceeds maximum length of "
                    f"{MAX_SKILL_DESCRIPTION_LENGTH} characters."
                )
            out["description"] = description

        # Content search-and-replace (old_string / new_string travel as a
        # pair; the actual match runs against the current content in the
        # pre-card check and again at execute time).
        has_old = params.get("old_string") is not None
        has_new = params.get("new_string") is not None
        if has_old != has_new:
            raise ValueError(
                "old_string and new_string must be provided together."
            )
        if has_old:
            old_string = params["old_string"]
            new_string = params["new_string"]
            if not isinstance(old_string, str) or not old_string:
                raise ValueError("old_string must be a non-empty string.")
            if not isinstance(new_string, str):
                raise ValueError(
                    "new_string must be a string (it may be empty to delete "
                    "the matched text)."
                )
            if old_string == new_string:
                raise ValueError(
                    "old_string and new_string are identical -- nothing to "
                    "change."
                )
            if len(new_string.encode("utf-8")) > MAX_SKILL_CONTENT_SIZE:
                raise ValueError(
                    f"new_string exceeds maximum skill content size of "
                    f"{MAX_SKILL_CONTENT_SIZE} bytes."
                )
            out["old_string"] = old_string
            out["new_string"] = new_string

        if "replace_all" in params and params["replace_all"] is not None:
            replace_all = params["replace_all"]
            if not isinstance(replace_all, bool):
                raise ValueError("replace_all must be a boolean.")
            if not has_old:
                raise ValueError(
                    "replace_all is only valid alongside old_string / "
                    "new_string."
                )
            out["replace_all"] = replace_all

        # visibility (optional)
        if "visibility" in params and params["visibility"] is not None:
            visibility = params["visibility"]
            if not isinstance(visibility, str) or visibility not in _USER_VISIBILITIES:
                raise ValueError(
                    f"Invalid visibility '{visibility}'. Must be one of: "
                    f"{sorted(_USER_VISIBILITIES)}."
                )
            out["visibility"] = visibility

        # share lists (optional)
        if "add_share_emails" in params:
            out["add_share_emails"] = _validate_email_list(
                params["add_share_emails"], "add_share_emails"
            )
        if "remove_share_emails" in params:
            out["remove_share_emails"] = _validate_email_list(
                params["remove_share_emails"], "remove_share_emails"
            )

        # In-call share/visibility contradiction (D3a). validate_params cannot
        # know the *current* visibility, so it only catches the contradiction
        # when the same call sets visibility to a non-shared value while also
        # carrying share mutations.
        if (
            out.get("visibility") is not None
            and out["visibility"] != "shared"
            and (out.get("add_share_emails") or out.get("remove_share_emails"))
        ):
            raise ValueError(
                "share changes are only valid when the skill is shared."
            )

        # project_autoload (optional bool; user-vs-project rejection deferred
        # to execute, like visibility / share params).
        if "project_autoload" in params:
            value = params["project_autoload"]
            if not isinstance(value, bool):
                raise ValueError("project_autoload must be a boolean.")
            out["project_autoload"] = value

        # Empty-payload rejection: skill_id alone is not an edit.
        if not (set(out) & _EDITABLE_FIELDS):
            raise ValueError(
                "No editable fields provided. Include at least one of: name, "
                "description, old_string/new_string, visibility, "
                "add_share_emails, remove_share_emails, project_autoload."
            )

        return out

    async def render_preview(self, params: dict, user: dict | None = None) -> list[dict]:
        skill_id = params.get("skill_id", "")
        # current_skill_name is server-injected by the dispatch arm (not in
        # _ALLOWED_PARAMS), parallel to calendar_name / entity_names.
        current_name = params.get("current_skill_name")
        header = current_name or f"Skill #{str(skill_id)[:8]}"
        fields: list[dict] = [{"key": "Skill", "value": str(header)}]

        if "name" in params:
            fields.append({"key": "Name", "value": str(params["name"])})
        if "description" in params:
            fields.append({"key": "Description", "value": str(params["description"])})
        if "old_string" in params:
            # content_diff is server-injected by the pre-card check (line
            # diff of the replacement applied to the current content).
            diff = params.get("content_diff")
            if isinstance(diff, dict) and isinstance(diff.get("lines"), list):
                fields.append({
                    "key": "Content",
                    "value": (
                        f"+{diff.get('added', 0)} / -{diff.get('removed', 0)} "
                        "line(s)"
                    ),
                    "type": "skill_content_diff",
                    "diff": diff,
                })
            else:
                # Fallback (no injected diff): show the raw replacement.
                fields.append(
                    {"key": "Replace", "value": str(params["old_string"])}
                )
                fields.append(
                    {"key": "With", "value": str(params["new_string"])}
                )
        elif "content" in params:
            # Legacy full-content rows proposed before the search-and-replace
            # interface.
            fields.append({"key": "Content", "value": str(params["content"])})
        if "visibility" in params:
            fields.append({"key": "Visibility", "value": str(params["visibility"])})
        if params.get("add_share_emails"):
            fields.append(
                {"key": "Add shares", "value": ", ".join(params["add_share_emails"])}
            )
        if params.get("remove_share_emails"):
            fields.append(
                {
                    "key": "Remove shares",
                    "value": ", ".join(params["remove_share_emails"]),
                }
            )
        if "project_autoload" in params:
            fields.append(
                {
                    "key": "Auto-load",
                    "value": "Enable" if params["project_autoload"] else "Disable",
                }
            )
        return fields

    async def execute(
        self,
        params: dict,
        user: dict,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        from db import skill_store, project_store, user_store

        skill_id = params["skill_id"]
        skill = await skill_store.get_skill(skill_id)
        if not skill:
            raise RuntimeError("Skill not found.")

        # Resolve a content search-and-replace against the LIVE content.
        # The pre-card check already verified the match at proposal time;
        # re-running it here closes the TOCTOU window (the skill may have
        # changed while the card sat open -- a stale old_string then fails
        # instead of clobbering the newer content). Legacy rows proposed
        # before this interface carry a full ``content`` param instead and
        # fall through untouched.
        if "old_string" in params:
            if conversation_id:
                from chat.storage import ChatStorage
                if skill_id not in ChatStorage.get_skill_read_ids(
                    conversation_id
                ):
                    raise RuntimeError(
                        "This skill's content has not been loaded or read "
                        "in this conversation. Read it with get_skill "
                        "before editing its content."
                    )
            try:
                new_content, _replacements = apply_content_edit(
                    skill.get("content") or "",
                    params["old_string"],
                    params["new_string"],
                    bool(params.get("replace_all")),
                )
            except ValueError as e:
                raise RuntimeError(str(e))
            params = {**params, "content": new_content}

        if skill.get("project_id"):
            return await self._execute_project_skill(
                params, user, skill, project_id, skill_store, project_store
            )
        return await self._execute_user_skill(
            params, user, skill, skill_store, user_store
        )

    async def _execute_project_skill(
        self, params, user, skill, project_id, skill_store, project_store
    ) -> dict:
        skill_id = params["skill_id"]
        skill_project_id = skill["project_id"]

        # Reject user-skill-only params.
        for forbidden in ("visibility", "add_share_emails", "remove_share_emails"):
            if forbidden in params:
                raise RuntimeError(
                    f"{forbidden} is not allowed for project skills."
                )

        # Access + project-match double gate.
        if not project_id or project_id != skill_project_id:
            raise RuntimeError("You do not have access to this project skill.")
        skill_project = await project_store.get_project(user["id"], skill_project_id)
        if skill_project is None:
            raise RuntimeError("You do not have access to this project skill.")
        if skill_project.get("public"):
            raise RuntimeError("Public projects cannot have project skills.")

        # Apply scalar field edits only when at least one is present.
        scalar_keys = ("name", "description", "content")
        if any(k in params for k in scalar_keys):
            try:
                updated = await skill_store.update_project_skill(
                    project_id=skill_project_id,
                    skill_id=skill_id,
                    name=params.get("name"),
                    description=params.get("description"),
                    content=params.get("content"),
                )
            except ValueError as e:
                raise RuntimeError(str(e))
            if updated is None:
                raise RuntimeError("You do not have access to this project skill.")

        # Auto-load toggle (D8).
        if "project_autoload" in params:
            await skill_store.set_project_skill_autoload(
                skill_project_id, skill_id, params["project_autoload"]
            )

        return {"success": True, "skill_id": skill_id, "target": "project"}

    async def _execute_user_skill(
        self, params, user, skill, skill_store, user_store
    ) -> dict:
        skill_id = params["skill_id"]

        # project_autoload only applies to project skills (D8).
        if "project_autoload" in params:
            raise RuntimeError(
                "auto-load toggling is only available for project skills."
            )

        # Effective visibility after this edit.
        effective_visibility = params.get("visibility") or skill.get("visibility")

        scalar_keys = ("name", "description", "content", "visibility")
        if any(k in params for k in scalar_keys):
            try:
                updated = await skill_store.update_skill(
                    creator_id=user["id"],
                    skill_id=skill_id,
                    name=params.get("name"),
                    description=params.get("description"),
                    content=params.get("content"),
                    visibility=params.get("visibility"),
                )
            except ValueError as e:
                raise RuntimeError(str(e))
            if updated is None:
                raise RuntimeError("Skill not found or you are not its owner.")
        else:
            # Only share lists change -> explicit ownership pre-check because
            # update_skill (the ownership gate) is not called in this branch.
            if skill.get("creator_id") != user["id"]:
                raise RuntimeError("Skill not found or you are not its owner.")

        # Share roster handling (D3a). The auto-clear takes precedence over any
        # add/remove params (which validate_params already forbade when the
        # edit sets visibility to non-shared).
        if effective_visibility != "shared":
            await skill_store.clear_skill_shares(skill_id)
        else:
            if params.get("remove_share_emails"):
                for email in params["remove_share_emails"]:
                    target_user = await user_store.get_user_by_email(email.strip())
                    if target_user:
                        await skill_store.remove_skill_share(
                            skill_id, target_user["id"]
                        )
            if params.get("add_share_emails"):
                resolved_ids: list[int] = []
                for email in params["add_share_emails"]:
                    target_user = await user_store.get_user_by_email(email.strip())
                    if target_user and target_user["id"] != user["id"]:
                        resolved_ids.append(target_user["id"])
                if resolved_ids:
                    await skill_store.add_skill_shares(skill_id, resolved_ids)

        return {"success": True, "skill_id": skill_id, "target": "user"}
