"""Filesystem plugin discovery, validation, and registry fan-out.

Plugins are directories under ``plugins/`` (repo root) whose ``plugin.py``
exports ``get_plugin() -> QuestPlugin``. At startup (quest.py, after the
core registries are populated) :func:`load_plugins` discovers, validates,
and registers each one. A plugin that raises on import, fails validation,
or errors during registration is logged and skipped -- a broken plugin must
never take down the server.

Discovery is a filesystem scan, not Python entry points: a fork adds a
proprietary integration by committing a directory into ``plugins/`` (and
un-ignoring it), with zero merge surface against upstream. Directories
whose name starts with ``_`` or ``.`` are skipped (``plugins/_example`` is
the in-repo smoke-test fixture, loaded only by tests).

Extra search roots can be supplied via the ``QUEST_PLUGIN_PATH``
environment variable -- an ``os.pathsep``-separated list of directories,
each traversed exactly like the in-tree ``plugins/`` dir
(``<root>/*/plugin.py``). The in-tree dir scans first, then the listed
roots in order, so on a duplicate plugin id the in-tree plugin wins (the
later duplicate fails validation and is skipped like any broken plugin).
External plugins are imported as standalone modules; unlike in-tree
plugins they cannot use ``plugins.<name>.*`` package imports, so a
multi-file external plugin manages its own imports (e.g. extends
``sys.path`` from its ``plugin.py``).

Namespacing rules enforced at load time:

- plugin ``id``: ``^[a-z][a-z0-9_]*$``, unique across plugins, and not a
  core credential-store service name.
- action-request type names and tool names: prefixed ``<id>_`` (except
  the ``unprefixed_action_types`` / ``unprefixed_tools`` grandfather
  lists for core-migrated integrations).
- system skill ids: ``system:<id>`` or ``system:<id>_...``.
- ``script_tool_allowlist``: subset of the plugin's own tool names.
- tool names must not collide with the public-project tool allowlist
  (plugin surface is always blocked in public projects), except the
  core-owned ``_PUBLIC_ALLOWLIST_MIGRATED_TOOLS`` exemptions.

Plugins do NOT mount ``/api/*`` HTTP routes (see config/plugin_types.py)
-- bespoke behavior lives in ``tools`` handlers instead. The one
exception: an oauth-kind user connection supplies a browser-facing
``oauth_router`` whose routes must all live under the plugin's
``/auth/<id>`` namespace (validated here, mounted by quest.py via
:func:`mount_plugin_oauth_routers` after load).

Two lifecycle hooks bracket a plugin's life: ``post_load`` runs once
right after registration (inside :func:`load_plugins`) and
``on_shutdown`` runs once from the app lifespan's shutdown phase via
:func:`shutdown_plugins`.
"""

import hashlib
import importlib.util
import logging
import os
import re
import sys
from pathlib import Path

from config.plugin_types import QuestPlugin

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
PLUGINS_DIR = PROJECT_ROOT / "plugins"

# os.pathsep-separated list of extra plugin search roots, each scanned
# like the in-tree plugins/ dir.
PLUGIN_PATH_ENV = "QUEST_PLUGIN_PATH"

_PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Tool names that live in the core public-project allowlist
# (chat.llm.tool_schemas.PUBLIC_TOOL_CALL_ALLOWLIST) but are served by a
# core-migrated plugin, mapped to the ONLY plugin id allowed to register
# them. Public-project availability stays a core decision -- the allowlist
# itself is core-owned and plugins cannot extend it -- this mapping merely
# exempts the named (tool, plugin) pairs from the "plugin tools never in
# the public allowlist" validation rule so the migrated tool keeps working.
_PUBLIC_ALLOWLIST_MIGRATED_TOOLS: dict[str, str] = {
    # Outbound-only self-DM with a fixed recipient; see the rationale on
    # the allowlist entry in chat/llm/tool_schemas.py.
    "send_slack_dm_to_self": "slack",
}

# Loaded-and-registered plugins, in load order.
_LOADED: list[QuestPlugin] = []


def get_loaded_plugins() -> tuple[QuestPlugin, ...]:
    """Return the successfully registered plugins, in load order."""
    return tuple(_LOADED)


def get_loaded_plugin(plugin_id: str) -> QuestPlugin | None:
    """Return the loaded plugin with the given id, or None."""
    for plugin in _LOADED:
        if plugin.id == plugin_id:
            return plugin
    return None


def plugin_server_available(plugin: QuestPlugin) -> bool:
    """Whether a plugin's server-level configuration makes it available.

    Mirrors the admin credential card's "configured" computation: a plugin
    with a ``credential_schema`` is available only when its store file
    exists and its ``is_configured`` predicate (default: any stored config
    counts) accepts the stored config. A plugin without a credential schema
    has no server-level switch and is always available.

    This is one half of the combined per-user gate: a plugin service counts
    as connected in ``get_user_connected_services()`` only when the server
    is configured AND the user is connected, so an admin disabling the
    server config immediately hides the plugin's skills/tools for keyed
    users instead of leaving them to fail at call time.
    """
    if not plugin.credential_schema:
        return True
    from config.service_credentials import read_service_credentials
    config = read_service_credentials(plugin.id)
    if config is None:
        return False
    predicate = plugin.is_configured or (lambda _config: True)
    try:
        return bool(predicate(config))
    except Exception:
        logger.exception(
            "Plugin %r is_configured predicate raised; treating as unavailable",
            plugin.id,
        )
        return False


def _extra_plugin_roots() -> list[Path]:
    """Extra plugin search roots from the QUEST_PLUGIN_PATH env var.

    An ``os.pathsep``-separated list of directories. Empty entries and
    repeats (including the in-tree ``plugins/`` dir) are dropped; an
    entry that is not a directory is logged and skipped so a typo'd path
    is visible instead of silently loading nothing.
    """
    roots: list[Path] = []
    seen: set[Path] = {PLUGINS_DIR.resolve()}
    for part in os.environ.get(PLUGIN_PATH_ENV, "").split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        root = Path(part).expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)
        if not root.is_dir():
            logger.warning(
                "%s entry %s is not a directory; skipping",
                PLUGIN_PATH_ENV, part,
            )
            continue
        roots.append(root)
    return roots


def _import_plugin_module(plugin_py: Path):
    """Import a plugin's plugin.py under a stable synthetic module name."""
    module_name = f"quest_plugin_{plugin_py.parent.name.lstrip('_')}"
    existing = sys.modules.get(module_name)
    if existing is not None \
            and getattr(existing, "__file__", None) != str(plugin_py):
        # A same-named directory in another search root already claimed
        # the name; disambiguate instead of clobbering its module.
        digest = hashlib.sha256(str(plugin_py).encode("utf-8")).hexdigest()[:8]
        module_name = f"{module_name}_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, plugin_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot build import spec for {plugin_py}")
    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules first so dataclasses / relative imports inside
    # the plugin module behave normally.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def validate_plugin(plugin: QuestPlugin, existing_ids: set[str]) -> None:
    """Validate a plugin manifest. Raises ValueError on any violation."""
    if not isinstance(plugin, QuestPlugin):
        raise ValueError(
            f"get_plugin() must return a QuestPlugin, got {type(plugin).__name__}"
        )
    pid = plugin.id
    if not _PLUGIN_ID_RE.match(pid or ""):
        raise ValueError(
            f"Plugin id {pid!r} is invalid (must match {_PLUGIN_ID_RE.pattern})"
        )
    if pid in existing_ids:
        raise ValueError(f"Duplicate plugin id: {pid!r}")
    from config.service_credentials import KNOWN_SERVICES
    if pid in KNOWN_SERVICES:
        raise ValueError(f"Plugin id {pid!r} collides with a core service name")
    if not (plugin.label or "").strip():
        raise ValueError(f"Plugin {pid!r} must have a non-empty label")

    prefix = f"{pid}_"

    # Action-request handlers: string type names, <id>_-prefixed unless
    # grandfathered via unprefixed_action_types (core-migrated plugins
    # whose type names are persisted in old action_requests rows).
    handler_type_names = {str(h.type_name) for h in plugin.action_request_handlers}
    for type_name in handler_type_names:
        if not type_name.startswith(prefix) \
                and type_name not in plugin.unprefixed_action_types:
            raise ValueError(
                f"Plugin {pid!r} action-request type {type_name!r} must be "
                f"prefixed {prefix!r}"
            )
    stray = set(plugin.unprefixed_action_types) - handler_type_names
    if stray:
        raise ValueError(
            f"Plugin {pid!r} unprefixed_action_types names types it does "
            f"not declare handlers for: {sorted(stray)}"
        )

    # Tools: spec shape + <id>_ prefix (unless grandfathered via
    # unprefixed_tools -- core-migrated plugins whose tool names are baked
    # into transcripts, sandbox scripts, and skill prose) + never in the
    # public allowlist (except the core-owned migration exemptions above).
    from chat.llm.tool_schemas import PUBLIC_TOOL_CALL_ALLOWLIST
    tool_names: set[str] = set()
    for tool in plugin.tools:
        name = tool.spec.get("name", "")
        if not name.startswith(prefix) and name not in plugin.unprefixed_tools:
            raise ValueError(
                f"Plugin {pid!r} tool {name!r} must be prefixed {prefix!r}"
            )
        if name in tool_names:
            raise ValueError(f"Plugin {pid!r} declares tool {name!r} twice")
        if name in PUBLIC_TOOL_CALL_ALLOWLIST \
                and _PUBLIC_ALLOWLIST_MIGRATED_TOOLS.get(name) != pid:
            raise ValueError(
                f"Plugin {pid!r} tool {name!r} collides with the "
                "public-project tool allowlist"
            )
        if not callable(tool.handler):
            raise ValueError(f"Plugin {pid!r} tool {name!r} handler is not callable")
        tool_names.add(name)
    stray_tools = set(plugin.unprefixed_tools) - tool_names
    if stray_tools:
        raise ValueError(
            f"Plugin {pid!r} unprefixed_tools names tools it does not "
            f"declare: {sorted(stray_tools)}"
        )

    if plugin.post_load is not None and not callable(plugin.post_load):
        raise ValueError(f"Plugin {pid!r} post_load is not callable")
    if plugin.on_shutdown is not None and not callable(plugin.on_shutdown):
        raise ValueError(f"Plugin {pid!r} on_shutdown is not callable")

    # Script allowlist: subset of the plugin's own tools.
    extra = set(plugin.script_tool_allowlist) - tool_names
    if extra:
        raise ValueError(
            f"Plugin {pid!r} script_tool_allowlist names tools it does not "
            f"declare: {sorted(extra)}"
        )

    # User connection: recognized kind + callable hooks + per-kind fields.
    user_connection = plugin.user_connection
    if user_connection is not None:
        if user_connection.kind not in ("api_key", "oauth"):
            raise ValueError(
                f"Plugin {pid!r} user_connection kind "
                f"{user_connection.kind!r} is unknown"
            )
        if not callable(user_connection.connected):
            raise ValueError(
                f"Plugin {pid!r} user_connection.connected is not callable"
            )
        if user_connection.validate_key is not None \
                and not callable(user_connection.validate_key):
            raise ValueError(
                f"Plugin {pid!r} user_connection.validate_key is not callable"
            )
        if user_connection.needs_reauth is not None \
                and not callable(user_connection.needs_reauth):
            raise ValueError(
                f"Plugin {pid!r} user_connection.needs_reauth is not callable"
            )
        if user_connection.kind == "api_key":
            if user_connection.oauth_router is not None:
                raise ValueError(
                    f"Plugin {pid!r} api_key user_connection must not set "
                    "oauth_router"
                )
        else:  # oauth
            for field in ("validate_key", "key_placeholder"):
                if getattr(user_connection, field) is not None:
                    raise ValueError(
                        f"Plugin {pid!r} oauth user_connection must not set "
                        f"{field} (api_key-only field)"
                    )
            router = user_connection.oauth_router
            routes = getattr(router, "routes", None)
            if router is None or routes is None:
                raise ValueError(
                    f"Plugin {pid!r} oauth user_connection must supply an "
                    "oauth_router (fastapi.APIRouter)"
                )
            # Confine the router to the plugin's /auth/<id> namespace so an
            # oauth plugin can never claim core or other plugins' routes.
            # route.path includes the router's own prefix.
            auth_prefix = f"/auth/{pid}"
            for route in routes:
                route_path = getattr(route, "path", "")
                if route_path != auth_prefix \
                        and not route_path.startswith(auth_prefix + "/"):
                    raise ValueError(
                        f"Plugin {pid!r} oauth_router route {route_path!r} "
                        f"is outside the plugin's {auth_prefix!r} namespace"
                    )
            if not all(
                isinstance(scope, str) and scope
                for scope in user_connection.scopes
            ):
                raise ValueError(
                    f"Plugin {pid!r} user_connection.scopes must be "
                    "non-empty strings"
                )

    # System skills: system:<id> namespace, plus the catalog's own static
    # checks (description length etc.) so a skill that would fail catalog
    # registration is rejected here, at validation time. Only the duplicate-id
    # check remains registration-only (it needs the live catalog state).
    from chat.system_skills import validate_system_skill_definition
    skill_prefix = f"system:{pid}"
    for skill in plugin.system_skills:
        if skill.id != skill_prefix and not skill.id.startswith(skill_prefix + "_"):
            raise ValueError(
                f"Plugin {pid!r} system skill id {skill.id!r} must be "
                f"{skill_prefix!r} or {skill_prefix!r}_..."
            )
        validate_system_skill_definition(skill)

    # Services: key + entry shape (authed_get re-validates on registration).
    for service in plugin.services:
        if not isinstance(service, dict) or not service.get("key") \
                or not isinstance(service.get("entry"), dict):
            raise ValueError(
                f"Plugin {pid!r} services entries must be "
                "{'key': '<hostname[/prefix]>', 'entry': {...}} dicts"
            )


def discover_plugins(
    plugins_dir: Path | None = None,
    include_hidden: bool = False,
) -> list[QuestPlugin]:
    """Scan plugin roots for ``*/plugin.py``, import, validate, return manifests.

    With ``plugins_dir=None`` the roots are the in-tree ``plugins/`` dir
    followed by the ``QUEST_PLUGIN_PATH`` extras in listed order (the
    in-tree dir scans first, so its plugins win duplicate-id conflicts);
    an explicit ``plugins_dir`` scans exactly that directory (tests).
    Broken or invalid plugins are logged and skipped. ``include_hidden``
    additionally scans ``_``-prefixed directories (used by tests to load
    the ``plugins/_example`` fixture).
    """
    if plugins_dir is not None:
        roots = [plugins_dir]
    else:
        roots = [PLUGINS_DIR, *_extra_plugin_roots()]
    plugins: list[QuestPlugin] = []
    seen_ids: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not include_hidden and child.name.startswith(("_", ".")):
                continue
            plugin_py = child / "plugin.py"
            if not plugin_py.is_file():
                continue
            try:
                module = _import_plugin_module(plugin_py)
                get_plugin = getattr(module, "get_plugin", None)
                if not callable(get_plugin):
                    raise ValueError("plugin.py does not export get_plugin()")
                plugin = get_plugin()
                validate_plugin(plugin, seen_ids)
            except Exception:
                logger.exception(
                    "Skipping broken plugin %s (import/validation failed)",
                    child,
                )
                continue
            seen_ids.add(plugin.id)
            plugins.append(plugin)
    return plugins


def register_plugin(plugin: QuestPlugin) -> None:
    """Fan a validated manifest out into the core registries.

    Not atomic: a failure partway leaves earlier registrations in place
    (callers treat the plugin as broken and skip it; the partial surface is
    harmless because every registration is namespaced by the plugin id).
    """
    from chat.system_skills import register_system_skill
    from chat.action_request_types.registry import register_handler
    from chat.llm.tool_schemas import (
        register_action_request_type,
        register_tool_call_tool,
    )
    from chat.gemini_api.tool_dispatch import register_tool_call_handler
    from chat.gemini_api.authed_get import register_service
    from chat.gemini_api.script_tool_call import extend_script_allowlist
    from config.service_specs import register_plugin_credentials

    # Admin credential surface: plugins that declare a credential_schema get
    # a generic Settings card and a data/service_credentials/<id>.json store
    # file (schema-driven -- see config/service_specs.py).
    if plugin.credential_schema:
        register_plugin_credentials(plugin)

    for service in plugin.services:
        register_service(service["key"], service["entry"])

    for skill in plugin.system_skills:
        register_system_skill(skill)

    for handler in plugin.action_request_handlers:
        register_handler(handler)
        register_action_request_type(str(handler.type_name))

    for tool in plugin.tools:
        spec = dict(tool.spec)
        if tool.requires_service:
            spec["requires_service"] = tool.requires_service
        if tool.mutating:
            spec["mutating"] = True
        register_tool_call_tool(spec)
        register_tool_call_handler(spec["name"], tool.handler)

    if plugin.script_tool_allowlist:
        extend_script_allowlist(plugin.script_tool_allowlist)


def unregister_plugin(plugin: QuestPlugin) -> None:
    """Best-effort inverse of :func:`register_plugin`, plus ``_LOADED`` removal.

    The server never unloads plugins at runtime; this exists for test
    suites (tests/plugin_support.py fixtures and the loader tests) that
    register a plugin into the live registries and must restore them
    afterwards. Keep the fan-out targets in sync with
    :func:`register_plugin`.
    """
    from chat.llm import tool_schemas
    from chat.gemini_api import script_tool_call, tool_dispatch
    from chat.gemini_api.authed_get import _SERVICE_REGISTRY
    from chat.system_skills import CATALOG
    from chat.action_request_types import registry as ar_registry

    import config.service_credentials as service_credentials
    import config.service_specs as service_specs

    if service_specs._PLUGIN_SPECS.pop(plugin.id, None) is not None:
        service_credentials.KNOWN_SERVICES = tuple(
            s for s in service_credentials.KNOWN_SERVICES if s != plugin.id
        )
    for service in plugin.services:
        _SERVICE_REGISTRY.pop(service["key"], None)
    for skill in plugin.system_skills:
        CATALOG.pop(skill.id, None)
    for handler in plugin.action_request_handlers:
        key = str(handler.type_name)
        ar_registry._REGISTRY.pop(key, None)
        if key in tool_schemas.ACTION_REQUEST_TYPE_ENUM:
            tool_schemas.ACTION_REQUEST_TYPE_ENUM.remove(key)
    tool_names = {tool.spec["name"] for tool in plugin.tools}
    for name in tool_names:
        tool_schemas.TOOL_CALL_REGISTRY.pop(name, None)
        tool_dispatch.TOOL_CALL_HANDLERS.pop(name, None)
    script_tool_call.SCRIPT_TOOL_CALL_ALLOWLIST = (
        script_tool_call.SCRIPT_TOOL_CALL_ALLOWLIST - tool_names
    )
    _LOADED[:] = [p for p in _LOADED if p.id != plugin.id]


def mount_plugin_oauth_routers(app) -> None:
    """Mount loaded plugins' oauth-kind connection routers onto the app.

    Called once from quest.py after :func:`load_plugins` (and before the
    SPA catch-all route is added). Validation has already confined every
    route to the plugin's ``/auth/<id>`` namespace, so a mount can never
    shadow core routes. A router that fails to mount is logged and
    skipped, matching the loader's broken-plugin posture (the plugin's
    other surfaces stay registered; its Connect flow 404s).
    """
    for plugin in get_loaded_plugins():
        spec = plugin.user_connection
        if spec is None or spec.kind != "oauth" or spec.oauth_router is None:
            continue
        try:
            app.include_router(spec.oauth_router)
        except Exception:
            logger.exception(
                "Failed to mount plugin %r oauth router; its Connect flow "
                "will 404", plugin.id,
            )


async def shutdown_plugins() -> None:
    """Run every loaded plugin's ``on_shutdown`` hook. Never raises.

    Called once from the quest.py lifespan's shutdown phase. Hooks run in
    reverse load order; a sync hook is called directly and a coroutine
    function's awaitable is awaited. A failing hook is logged and the
    remaining hooks still run -- teardown must never abort the shutdown.
    """
    import inspect

    for plugin in reversed(_LOADED):
        hook = plugin.on_shutdown
        if hook is None:
            continue
        try:
            result = hook()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Plugin %r on_shutdown hook failed", plugin.id)


def load_plugins(plugins_dir: Path | None = None) -> tuple[QuestPlugin, ...]:
    """Discover and register all plugins. Never raises.

    Called once from quest.py after the core registries are populated.
    Returns the successfully registered plugins.
    """
    for plugin in discover_plugins(plugins_dir=plugins_dir):
        try:
            register_plugin(plugin)
        except Exception:
            logger.exception(
                "Skipping plugin %r (registration failed)", plugin.id,
            )
            continue
        _LOADED.append(plugin)
        if plugin.post_load is not None:
            # Plugin-owned one-time startup work (e.g. legacy credential
            # migrations). A failing hook is logged but the plugin stays
            # loaded -- its registered surfaces are already live.
            try:
                plugin.post_load()
            except Exception:
                logger.exception(
                    "Plugin %r post_load hook failed", plugin.id,
                )
        logger.info(
            "Loaded plugin %r (%s): %d skill(s), %d action type(s), "
            "%d tool(s), %d service(s)",
            plugin.id, plugin.label,
            len(plugin.system_skills), len(plugin.action_request_handlers),
            len(plugin.tools), len(plugin.services),
        )
    return get_loaded_plugins()
