"""Smoke-test example plugin exercising every QuestPlugin manifest field.

NOT loaded in production: discovery skips ``_``-prefixed directories. The
test suite (tests/test_plugin_loader.py) loads it explicitly with
``include_hidden=True`` / direct import to verify the loader's fan-out into
every core registry. It also doubles as a minimal authoring reference next
to plugins/README.md.
"""

import json

from chat.action_request_types.base import ActionRequestHandler
from chat.system_skills import SystemSkill
from config.plugin_types import (
    CredentialField,
    PluginTool,
    QuestPlugin,
    UserConnectionSpec,
)

# ---------------------------------------------------------------------------
# Upstream service (authed_get registry entry)
# ---------------------------------------------------------------------------


def _load_example_credentials():
    """No-auth loader (mirrors the Federal Register / SEC EDGAR modeling)."""
    return None


def _inject_example_auth(headers: dict, _credentials) -> dict:
    return headers


_EXAMPLE_SERVICE = {
    "key": "api.example.com",
    "entry": {
        "name": "Example API",
        "load_credentials": _load_example_credentials,
        "inject_auth": _inject_example_auth,
        "allowed_endpoints": [
            r"^/v1/widgets$",
            r"^/v1/widgets/[^/]+$",
        ],
        "allowed_post_endpoints": [
            r"^/v1/widgets:search$",
        ],
    },
}


# ---------------------------------------------------------------------------
# Action-request handler
# ---------------------------------------------------------------------------


class ExampleEchoHandler(ActionRequestHandler):
    """Trivial write handler: echoes its message back on approval."""

    @property
    def type_name(self) -> str:
        return "example_echo"

    @property
    def display_name(self) -> str:
        return "Example Echo"

    @property
    def preview_fields(self) -> list[str]:
        return ["message"]

    def validate_params(self, params: dict) -> dict:
        message = params.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        unknown = set(params) - {"message"}
        if unknown:
            raise ValueError(f"Unknown parameters: {sorted(unknown)}")
        return {"message": message.strip()}

    async def execute(
        self,
        params: dict,
        user: dict,
        *,
        conversation_id: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        return {"echoed": params["message"]}


# ---------------------------------------------------------------------------
# Dynamic tool (tool_call-routed)
# ---------------------------------------------------------------------------


async def _handle_example_ping(ctx, args: dict) -> str:
    """Handler signature: (ToolContext, inner args) -> result string."""
    return json.dumps({"pong": args.get("value", "")})


_EXAMPLE_PING_TOOL = PluginTool(
    spec={
        "name": "example_ping",
        "description": "Echo a value back (example plugin smoke tool).",
        "parameters": {
            "type": "object",
            "properties": {
                "value": {
                    "type": "string",
                    "description": "Any string to echo back.",
                },
                "intent_message": {
                    "type": "string",
                    "description": "A brief, user-friendly summary of your intent (max 50 characters).",
                },
            },
            "required": [],
        },
    },
    handler=_handle_example_ping,
    requires_service="example",
)


# ---------------------------------------------------------------------------
# System skill
# ---------------------------------------------------------------------------


def _example_skill_content(_base_url: str, _api_key: str) -> str:
    return (
        "## Example plugin\n\n"
        "Use `tool_call(tool_name=\"example_ping\", arguments={\"value\": ...})` "
        "to ping, and `create_action_request(request_type=\"example_echo\", "
        "params={\"message\": ...})` to propose an echo."
    )


# ---------------------------------------------------------------------------
# Startup hook
# ---------------------------------------------------------------------------


def _example_post_load() -> None:
    """No-op startup hook (real plugins use this for e.g. legacy credential
    migrations run once at load time)."""


async def _example_on_shutdown() -> None:
    """No-op shutdown hook (real plugins close long-lived upstream client
    connections here; a plain sync function works too)."""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def get_plugin() -> QuestPlugin:
    return QuestPlugin(
        id="example",
        label="Example Plugin",
        credential_schema=(
            CredentialField(key="enabled", label="Enabled", type="bool"),
            CredentialField(
                key="base_url", label="Base URL", type="text",
                placeholder="https://api.example.com",
                required_if="enabled", visible_if="enabled",
            ),
            CredentialField(key="api_token", label="API Token", type="secret"),
        ),
        is_configured=lambda creds: bool(creds.get("enabled")),
        user_connection=UserConnectionSpec(
            kind="api_key",
            connected=lambda row: bool(row.get("secret")),
            validate_key=lambda key: None if len(key) >= 8 else "Key too short",
        ),
        services=(_EXAMPLE_SERVICE,),
        system_skills=(
            SystemSkill(
                id="system:example",
                name="Example",
                description="Example plugin smoke skill (ping tool + echo action request).",
                when_to_load="Load when testing the example plugin.",
                requires="example",
                content_builder=_example_skill_content,
            ),
        ),
        action_request_handlers=(ExampleEchoHandler(),),
        tools=(_EXAMPLE_PING_TOOL,),
        script_tool_allowlist=frozenset({"example_ping"}),
        post_load=_example_post_load,
        on_shutdown=_example_on_shutdown,
    )
