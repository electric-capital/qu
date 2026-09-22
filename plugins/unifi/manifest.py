"""The UniFi (Network + Protect) integration plugin manifest.

Plugin id ``unifi``: the ``connected_services`` key, the ``system:unifi``
skill gate, the admin credential store file (``unifi.json``: controller
host + TLS-verification switch), and the router namespace (``/auth/unifi``).

Shape notes:

1. **Admin config is just the host.** The plugin talks directly to a UniFi
   OS console on the local network (never through unifi.ui.com); the admin
   enters ``https://<controller>`` once and optionally turns certificate
   verification on (off by default -- consoles ship self-signed certs).
2. **Per-user API keys, two of them.** UniFi issues a separate API key per
   application (Network, Protect), and the generic ``api_key`` connection
   kind stores one secret, so the connection is declared as the ``oauth``
   kind -- the manifest shape that mounts a plugin-owned popup page under
   ``/auth/unifi`` (plugins/unifi/connect.py) where the user pastes one or
   both keys; each is tested against the controller before it is stored
   in the ``user_service_credentials`` row's ``oauth_blob``.
3. **Read-only.** Every tool is a GET (snapshots included); nothing on the
   controller is changed, so no action requests are declared.
"""

from pathlib import Path

from chat.system_skills import SystemSkill
from config.plugin_types import CredentialField, QuestPlugin, UserConnectionSpec

from plugins.unifi.connect import router as unifi_router
from plugins.unifi.tools import SCRIPT_TOOL_ALLOWLIST, UNIFI_TOOLS
from plugins.unifi.upstream import (
    unifi_connected,
    unifi_is_configured,
    validate_unifi_credentials,
)

_PLUGIN_DIR = Path(__file__).parent


def _unifi_skill_content(_base_url: str, _api_key: str) -> str:
    """The system:unifi skill body, from the instructions.md data file."""
    return (_PLUGIN_DIR / "instructions.md").read_text()


def get_plugin() -> QuestPlugin:
    return QuestPlugin(
        id="unifi",
        label="UniFi",
        credential_schema=(
            CredentialField(
                key="host", label="Controller host", type="text",
                placeholder="https://192.168.1.1",
                required=True,
            ),
            CredentialField(
                key="verify_tls",
                label=(
                    "Verify the controller's TLS certificate (leave off for "
                    "the default self-signed certificate)"
                ),
                type="bool",
            ),
        ),
        is_configured=unifi_is_configured,
        credential_validate=validate_unifi_credentials,
        user_connection=UserConnectionSpec(
            kind="oauth",
            connected=unifi_connected,
            oauth_router=unifi_router,
        ),
        system_skills=(
            SystemSkill(
                id="system:unifi",
                name="UniFi",
                description=(
                    "UniFi Network (status, devices, clients) and Protect "
                    "(cameras, live snapshots) via unifi_* tools."
                ),
                when_to_load=(
                    "Load when the user asks about their UniFi network, "
                    "internet/WAN status, access points, switches, connected "
                    "clients, or Protect cameras / snapshots."
                ),
                requires="unifi",
                content_builder=_unifi_skill_content,
            ),
        ),
        tools=UNIFI_TOOLS,
        script_tool_allowlist=SCRIPT_TOOL_ALLOWLIST,
    )
