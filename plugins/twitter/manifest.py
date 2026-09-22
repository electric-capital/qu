"""The Twitter/X integration plugin manifest.

An oauth-kind plugin (like plugins/m365) whose tokens EXPIRE and whose
refresh tokens ROTATE: X access tokens live ~2 hours, so the credential
loader proactively refreshes (see plugins/twitter/upstream.py) and the
``api.twitter.com`` authed_get entry sets ``retry_on_401`` as a backstop.
The OAuth flow is the repo's only PKCE flow (S256 code challenge, see
plugins/twitter/oauth.py).

The plugin id is ``twitter``: the ``connected_services`` key, the
``system:twitter`` skill gate, the admin credential store file
(``twitter.json``: OAuth 2.0 app client id/secret, with the legacy
standalone twitter_credentials.json fallback preserved in
config/service_credentials.py), and the OAuth namespace
(``/auth/twitter`` -- the same URLs as pre-plugin).

The ``send_twitter_dm`` action-request type predates the packaging and is
persisted in old ``action_requests`` rows, so it rides on the
``unprefixed_action_types`` grandfather list.
"""

from pathlib import Path

from chat.system_skills import SystemSkill
from config.plugin_types import CredentialField, QuestPlugin, UserConnectionSpec

from plugins.twitter.handlers import SendTwitterDmHandler
from plugins.twitter.oauth import router as twitter_oauth_router
from plugins.twitter.upstream import (
    MISSING_CREDENTIALS_ERROR,
    TWITTER_SCOPES,
    inject_twitter_bearer_auth,
    load_twitter_credentials,
    twitter_connected,
    twitter_is_configured,
    twitter_needs_reauth,
    validate_twitter_credentials,
)

_PLUGIN_DIR = Path(__file__).parent


def _twitter_skill_content(_base_url: str, _api_key: str) -> str:
    """The system:twitter skill body, from the instructions.md data file."""
    return (_PLUGIN_DIR / "instructions.md").read_text()


# authed_get service entry for raw (read-only) X API v2 access -- own
# profile, user lookup, DM events, bookmarks, tweet lookup. Read-only by
# construction: only these GET paths are reachable and the entry defines
# no allowed_post_endpoints (the one write, sending a DM, goes through
# the gated send_twitter_dm action request). ``[^/:]+`` id segments are
# defensive parity with the GCP entries; X ids are numeric and never
# contain '/' or ':'.
_TWITTER_SERVICE = {
    "key": "api.twitter.com",
    "entry": {
        "name": "Twitter/X",
        "load_credentials": load_twitter_credentials,
        "inject_auth": inject_twitter_bearer_auth,
        "requires_user": True,
        "retry_on_401": True,
        "missing_credentials_error": MISSING_CREDENTIALS_ERROR,
        "allowed_endpoints": [
            r"^/2/users/me$",                                # own profile (id for DM sender matching / bookmarks path)
            r"^/2/users/[^/:]+$",                            # user lookup by id (matches /2/users/me too)
            r"^/2/dm_events$",                               # list recent DM events across conversations
            r"^/2/dm_conversations/[^/:]+$",                 # get a conversation (participants via expansions)
            r"^/2/dm_conversations/[^/:]+/dm_events$",       # events in a specific conversation
            r"^/2/users/[^/:]+/bookmarks$",                  # list bookmarked tweets
            r"^/2/tweets/[^/:]+$",                           # tweet lookup by id
        ],
    },
}


def get_plugin() -> QuestPlugin:
    return QuestPlugin(
        id="twitter",
        label="Twitter / X",
        credential_schema=(
            CredentialField(
                key="client_id", label="Client ID", type="text",
                placeholder="Twitter/X OAuth 2.0 client ID",
                required=True,
            ),
            CredentialField(
                key="client_secret", label="Client secret", type="secret",
                placeholder="Twitter/X OAuth 2.0 client secret",
                required=True,
            ),
        ),
        is_configured=twitter_is_configured,
        credential_validate=validate_twitter_credentials,
        user_connection=UserConnectionSpec(
            kind="oauth",
            connected=twitter_connected,
            oauth_router=twitter_oauth_router,
            scopes=TWITTER_SCOPES + ("offline.access",),
            needs_reauth=twitter_needs_reauth,
        ),
        services=(_TWITTER_SERVICE,),
        system_skills=(
            SystemSkill(
                id="system:twitter",
                name="Twitter/X",
                description=(
                    "DMs, bookmarks, tweet lookup via authed_get; "
                    "send_twitter_dm action."
                ),
                when_to_load=(
                    "Load when the user asks about Twitter/X DMs, "
                    "bookmarks, or tweets."
                ),
                requires="twitter",
                content_builder=_twitter_skill_content,
            ),
        ),
        action_request_handlers=(SendTwitterDmHandler(),),
        unprefixed_action_types=frozenset({"send_twitter_dm"}),
    )
