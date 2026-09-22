"""Hardcoded "system" skills loadable on demand via the load_skills tool.

System skills carry per-backend API documentation that used to be inlined in
the system prompt on every turn. They are addressed by a stable identifier of
the form ``system:<name>`` (e.g. ``system:slack``). They are not stored in the
database and are not user-editable; the catalog lives in
``chat/system_skills/catalog.py``.

Public surface:

- :data:`CATALOG` -- mapping of system-skill id to :class:`SystemSkill`.
- :func:`list_system_skills` -- enumerate skills available to a user
  (gated by their connected services / project status).
- :func:`load_system_skills` -- resolve full skill content for a list of ids.
- :func:`build_system_skills_enumeration` -- format the compact listing
  embedded in the main system prompt.
- :func:`is_system_skill_id` -- predicate used by tool handlers to route
  ``system:`` ids to this module instead of the DB.
"""

from chat.system_skills.catalog import (
    CATALOG,
    SystemSkill,
    register_system_skill,
    validate_system_skill_definition,
)
from chat.system_skills.loader import (
    build_system_skills_enumeration,
    is_system_skill_id,
    list_system_skills,
    load_system_skills,
)

__all__ = [
    "CATALOG",
    "SystemSkill",
    "build_system_skills_enumeration",
    "is_system_skill_id",
    "list_system_skills",
    "load_system_skills",
    "register_system_skill",
    "validate_system_skill_definition",
]
