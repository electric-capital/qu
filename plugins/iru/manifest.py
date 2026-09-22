"""The Iru (formerly Kandji) integration plugin manifest.

Plugin id ``iru``: the ``connected_services`` key, the ``system:iru`` skill
gate, the admin credential store file (``iru.json``: the tenant API URL),
and the per-user ``api_key`` connection (the generic
``POST /auth/service-key/iru`` routes).

Shape notes:

1. **Admin config is just the tenant URL.** Every Iru tenant has its own
   API host (``https://<subdomain>.api.iru.com`` or the EU / legacy
   ``kandji.io`` variants), so the upstream host is admin-configured at
   runtime and everything is a ``tools`` handler (a static-hostname
   ``services`` entry cannot express it -- the UniFi precedent).
2. **Per-user API token, the generic api_key kind.** Iru API tokens are
   tenant-level bearer tokens minted in Settings > Access > API Token with
   per-endpoint permissions. Each Quest user pastes their own token, so
   access follows whatever an Iru admin granted that token; a token
   without a permission answers ``iru_auth_failed`` (403) on that tool.
3. **Read side only, for now.** Every tool is a GET. Device actions
   (restart, lock, erase, blank push, ...) and record edits are deliberately
   not exposed yet; they will ride on approval-gated action requests. The
   device *secrets* GETs (FileVault key, activation-lock bypass code,
   recovery-lock password, unlock PIN) are also left out on purpose: they
   would land in conversation transcripts.
"""

from pathlib import Path

from chat.system_skills import SystemSkill
from config.plugin_types import CredentialField, QuestPlugin, UserConnectionSpec

from plugins.iru.tools import IRU_TOOLS, SCRIPT_TOOL_ALLOWLIST
from plugins.iru.upstream import (
    iru_connected,
    iru_is_configured,
    validate_api_token,
    validate_iru_credentials,
)

_PLUGIN_DIR = Path(__file__).parent


def _iru_skill_content(_base_url: str, _api_key: str) -> str:
    """The system:iru skill body, from the instructions.md data file."""
    return (_PLUGIN_DIR / "instructions.md").read_text()


def get_plugin() -> QuestPlugin:
    return QuestPlugin(
        id="iru",
        label="Iru (Kandji)",
        credential_schema=(
            CredentialField(
                key="api_url", label="Tenant API URL", type="text",
                placeholder="https://acme.api.iru.com",
                required=True,
            ),
        ),
        is_configured=iru_is_configured,
        credential_validate=validate_iru_credentials,
        user_connection=UserConnectionSpec(
            kind="api_key",
            connected=iru_connected,
            validate_key=validate_api_token,
            key_placeholder="Paste your Iru API token",
        ),
        system_skills=(
            SystemSkill(
                id="system:iru",
                name="Iru (Kandji)",
                description=(
                    "Iru / Kandji MDM: devices, blueprints, library items, users, "
                    "Prism reports, threats, vulns via iru_* tools."
                ),
                when_to_load=(
                    "Load when the user asks about their Macs / iPhones / iPads "
                    "managed in Iru or Kandji (MDM): device inventory, a specific "
                    "device's status or installed apps, blueprints, library item "
                    "deployment status, FileVault / OS version reports, threats, "
                    "vulnerabilities, ADE devices, or the Iru audit log."
                ),
                requires="iru",
                content_builder=_iru_skill_content,
            ),
        ),
        tools=IRU_TOOLS,
        script_tool_allowlist=SCRIPT_TOOL_ALLOWLIST,
    )
