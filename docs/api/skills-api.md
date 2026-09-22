# Skills API Documentation

This document describes the REST API endpoints for the Skill Library feature, including both user-level skill management and project-level skill management.

## Overview

The Skills API provides CRUD operations for skills and share management. Skills are reusable instruction/prompt definitions that can be private, shared with specific users, public, or scoped to a project. All endpoints require authentication (session cookie or Bearer token). User-level skill endpoints are in `chat/skill_routes.py`; project-level skill endpoints are in `chat/project_skill_routes.py`.

## Key Files

| File | Description |
|------|-------------|
| `chat/skill_routes.py` | REST API endpoint implementations for user-level skills (`/app/api/skills`) |
| `chat/project_skill_routes.py` | REST API endpoint implementations for project skills (`/app/api/projects/{project_id}/skills`) |
| `db/skill_store.py` | Skill data access layer (CRUD, sharing, access checks, user auto-load, project skill CRUD, project auto-load) |
| `db/models.py` | `Skill`, `SkillShare`, `UserSkillAutoload`, `ProjectSkillAutoload` ORM models; `SkillVisibility` enum |

## Authentication

All endpoints use dual authentication (session cookie or Bearer token), matching the pattern described in [Chat API](chat-api.md).

## User-Level Skill Endpoints

All user-level skill endpoints are defined in `chat/skill_routes.py`. Request/response models (Pydantic) are in the same file. Data access is in `db/skill_store.py`. Access control uses `user_can_access_skill()` in `db/skill_store.py`.

### CRUD

- **POST `/app/api/skills`** -- Create skill (`create_user_skill`). Visibility: `"private"`, `"shared"`, or `"public"`.
- **GET `/app/api/skills`** -- List skills (`list_skills`). Without filters, returns the union of own + public + shared skills. Supports `owned` and `visibility` query params.
- **GET `/app/api/skills/{skill_id}`** -- Get skill (`get_user_skill`). Access-checked.
- **PUT `/app/api/skills/{skill_id}`** -- Update skill (`update_user_skill`). Creator only.
- **DELETE `/app/api/skills/{skill_id}`** -- Delete skill (`delete_user_skill`). Creator only.

### Search

- **GET `/app/api/skills/search`** -- Search skills by keyword (`search_skills_endpoint`). Case-insensitive substring match on name and description. Returns only accessible skills.

### Auto-load

- **GET `/app/api/skills/autoloaded`** -- Get auto-loaded skill IDs (`get_autoloaded_skill_ids`). Reads from `user_skill_autoloads` table.
- **GET `/app/api/skills/shared-with-me`** -- Get skills accessible to user but not created by them (`get_shared_with_me_skills`).
- **PUT `/app/api/skills/{skill_id}/autoload`** -- Toggle auto-load (`toggle_skill_autoload`). Users can auto-load any skill they have access to (own, shared, or public).

### Share Management

- **POST `/app/api/skills/{skill_id}/shares`** -- Add shares (`add_skill_shares_endpoint`). Creator only. Unknown emails and duplicates are silently skipped. Email resolution via `get_user_by_email()` in `db/user_store.py`.
- **GET `/app/api/skills/{skill_id}/shares`** -- List shares (`list_skill_shares_endpoint`). Creator only.
- **DELETE `/app/api/skills/{skill_id}/shares/{target_user_id}`** -- Remove share (`remove_skill_share_endpoint`). Creator only.

### User Search

- **GET `/app/api/users/search`** -- Search users by name/email (`search_users_endpoint`). Used by skill sharing type-ahead autocomplete. Results limited to 10, authenticated user excluded.

## Project Skill Endpoints

All project skill endpoints are defined in `chat/project_skill_routes.py` under `/app/api/projects/{project_id}/skills`. Every endpoint verifies project ownership.

- **GET `.../skills`** -- List project skills (`list_skills_for_project`). Visibility is always `"project"`.
- **POST `.../skills`** -- Create project skill (`create_skill_for_project`). Visibility auto-set to `"project"`. Names must be unique within the project.
- **GET `.../skills/autoloaded`** -- Get project auto-loaded skill IDs (`get_project_autoloaded_skills`). Stored in `project_skill_autoloads` table.
- **GET `.../skills/{skill_id}`** -- Get project skill (`get_skill_for_project`).
- **PUT `.../skills/{skill_id}`** -- Update project skill (`update_skill_for_project`).
- **DELETE `.../skills/{skill_id}`** -- Delete project skill (`delete_skill_for_project`).
- **PUT `.../skills/{skill_id}/autoload`** -- Toggle project skill auto-load (`toggle_project_skill_autoload`). Can include project-specific skills, user-level skills, and shared/public skills; enabling any other skill id returns 404 `not_found` (disabling never requires access).

## Agent Tools

In addition to these REST endpoints, the conversation loop exposes read-only agent tools over the same skill data: `list_skills` / `search_skills` / `load_skills` (discovery and loading, including `system:*` skills) and `list_my_skills` / `get_skill` (categorized inspection of the user's DB skills with autoload status and the owner-only share roster). These are not REST endpoints; they are defined in `BASE_TOOLS` in `chat/llm/tool_schemas.py` and handled in `chat/gemini_api/tool_handlers/skills.py`. See [Skill Library -- LLM Skill Tools](../architecture/skill-library.md#llm-skill-tools).

The agent **writes** skills (create / edit, including visibility, sharing, and project auto-load) through the generic `create_action_request` tool, not these REST endpoints -- the `create_skill` / `edit_skill` request types are handled by `CreateSkillHandler` / `EditSkillHandler` in `chat/action_request_types/`, mutating the same `db/skill_store.py` layer after user approval. See [Skill Library -- Creating and Editing Skills via Action Requests](../architecture/skill-library.md#creating-and-editing-skills-via-action-requests) and [Action Requests](../architecture/action-requests.md).