"""The Microsoft 365 (Outlook Mail) plugin manifest.

An oauth-kind plugin (like plugins/github) whose tokens EXPIRE: the
Microsoft identity platform issues ~1-hour access tokens plus a refresh
token (``offline_access``), so the credential loader proactively
refreshes (see plugins/m365/upstream.py) and the ``graph.microsoft.com``
authed_get entry sets ``retry_on_401`` as a backstop -- the Ramp pattern
on the GitHub plugin shape.

The plugin id is ``m365``: the ``connected_services`` key, the
``system:m365`` skill gate, the ``m365_*`` tool prefix, the admin
credential store file (``m365.json``: Entra ID tenant id + app client
id/secret), and the OAuth namespace (``/auth/m365``).

Scope: Outlook MAIL only for now. The Graph service entry and tool
surface deliberately cover just ``/me/messages`` + ``/me/mailFolders``;
other Microsoft 365 services (calendar, OneDrive, ...) would extend this
plugin with more allow-list entries and tools.
"""

from pathlib import Path

from chat.system_skills import SystemSkill
from config.plugin_types import CredentialField, QuestPlugin, UserConnectionSpec

from plugins.m365.oauth import router as m365_oauth_router
from plugins.m365.tools import ALL_TOOLS, SCRIPT_TOOL_NAMES
from plugins.m365.upstream import (
    M365_SCOPES,
    MISSING_CREDENTIALS_ERROR,
    inject_m365_bearer_auth,
    load_m365_credentials,
    m365_connected,
    m365_is_configured,
    m365_needs_reauth,
    validate_m365_credentials,
)

_PLUGIN_DIR = Path(__file__).parent


def _m365_skill_content(_base_url: str, _api_key: str) -> str:
    """The system:m365 skill body, from the instructions.md data file."""
    return (_PLUGIN_DIR / "instructions.md").read_text()


# authed_get service entry for raw (read-only) Microsoft Graph mail
# access -- list/search message ids, folder browsing, attachment
# metadata. Read-only by construction: only these GET paths are
# reachable and the entry defines no allowed_post_endpoints (all writes
# go through the gated m365_* tools). ``[^/:]+`` id segments are
# defensive parity with the GCP entries; Graph ids never contain '/'
# or ':'.
_GRAPH_SERVICE = {
    "key": "graph.microsoft.com",
    "entry": {
        "name": "Microsoft Graph (Outlook Mail)",
        "load_credentials": load_m365_credentials,
        "inject_auth": inject_m365_bearer_auth,
        "requires_user": True,
        "retry_on_401": True,
        "missing_credentials_error": MISSING_CREDENTIALS_ERROR,
        "allowed_endpoints": [
            r"^/v1\.0/me$",                                            # connected mailbox identity
            r"^/v1\.0/me/messages$",                                   # list/search messages ($search/$filter/$top)
            r"^/v1\.0/me/messages/[^/:]+$",                            # get a single message
            r"^/v1\.0/me/messages/[^/:]+/attachments$",                # list attachment metadata
            r"^/v1\.0/me/messages/[^/:]+/attachments/[^/:]+$",         # get one attachment (JSON incl. contentBytes)
            r"^/v1\.0/me/mailFolders$",                                # list top-level folders
            r"^/v1\.0/me/mailFolders/[^/:]+$",                         # get a folder (id or well-known name)
            r"^/v1\.0/me/mailFolders/[^/:]+/messages$",                # list/search messages in a folder
            r"^/v1\.0/me/mailFolders/[^/:]+/messages/[^/:]+$",         # get a message via its folder
            r"^/v1\.0/me/mailFolders/[^/:]+/childFolders$",            # list child folders
        ],
    },
}


def get_plugin() -> QuestPlugin:
    return QuestPlugin(
        id="m365",
        label="Microsoft 365",
        credential_schema=(
            CredentialField(
                key="tenant_id", label="Directory (tenant) ID", type="text",
                placeholder="Entra ID tenant GUID (or 'organizations')",
                required=True,
            ),
            CredentialField(
                key="client_id", label="Application (client) ID", type="text",
                placeholder="Entra ID app registration client ID",
                required=True,
            ),
            CredentialField(
                key="client_secret", label="Client secret", type="secret",
                placeholder="Entra ID app registration client secret value",
                required=True,
            ),
        ),
        is_configured=m365_is_configured,
        credential_validate=validate_m365_credentials,
        user_connection=UserConnectionSpec(
            kind="oauth",
            connected=m365_connected,
            oauth_router=m365_oauth_router,
            scopes=("offline_access",) + M365_SCOPES,
            needs_reauth=m365_needs_reauth,
        ),
        services=(_GRAPH_SERVICE,),
        system_skills=(
            SystemSkill(
                id="system:m365",
                name="Microsoft 365 Mail (Outlook)",
                description=(
                    "Outlook email via Microsoft Graph: m365_* "
                    "read/draft/archive tools plus raw read-only Graph "
                    "access via authed_get."
                ),
                when_to_load=(
                    "Load when the user asks about their Outlook / "
                    "Microsoft 365 / Office 365 email, folders, or "
                    "attachments."
                ),
                requires="m365",
                content_builder=_m365_skill_content,
            ),
        ),
        tools=ALL_TOOLS,
        script_tool_allowlist=SCRIPT_TOOL_NAMES,
    )
