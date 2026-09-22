"""The Telegram integration plugin manifest.

Plugin id ``telegram``: the ``connected_services`` key, the
``system:telegram`` skill gate, the admin credential store file
(``telegram.json``: the MTProto app ``api_id`` / ``api_hash`` from
my.telegram.org, with the legacy ``telegram_app_info`` section of
server_config.json migrated by the ``post_load`` hook), and the router
namespace (``/auth/telegram``).

Three things distinguish it from the OAuth plugins:

1. **No OAuth.** The per-user connection is a Telethon session obtained
   through Telegram's own login (phone number -> code sent to the Telegram
   app -> optional cloud password), run by plugins/telegram/auth.py. The
   connection is still declared as the ``oauth`` kind because that is the
   manifest shape that mounts a plugin-owned, session-cookie-authed router
   under ``/auth/<id>`` and renders the generic Connect-popup row.
2. **A long-lived upstream connection.** Telethon clients are kept
   connected per user for the life of the process
   (``TelegramClientManager``), so the manifest supplies an
   ``on_shutdown`` hook that closes them all.
3. **A grandfathered action type.** ``send_telegram_message`` predates the
   plugin packaging and is persisted in old ``action_requests`` rows, so it
   is listed in ``unprefixed_action_types``. The four read tools already
   carry the ``telegram_`` prefix.
"""

from pathlib import Path

from chat.system_skills import SystemSkill
from config.plugin_types import CredentialField, QuestPlugin, UserConnectionSpec

from plugins.telegram.auth import router as telegram_router
from plugins.telegram.handlers import SendTelegramMessageHandler
from plugins.telegram.tools import TELEGRAM_TOOL_NAMES, TELEGRAM_TOOLS
from plugins.telegram.upstream import (
    close_all_clients,
    migrate_legacy_credentials,
    telegram_connected,
    telegram_is_configured,
    validate_telegram_credentials,
)

_PLUGIN_DIR = Path(__file__).parent


def _telegram_skill_content(_base_url: str, _api_key: str) -> str:
    """The system:telegram skill body, from the instructions.md data file."""
    return (_PLUGIN_DIR / "instructions.md").read_text()


def get_plugin() -> QuestPlugin:
    return QuestPlugin(
        id="telegram",
        label="Telegram",
        credential_schema=(
            CredentialField(
                key="api_id", label="API ID", type="text",
                placeholder="App api_id from my.telegram.org/apps", required=True,
            ),
            CredentialField(
                key="api_hash", label="API hash", type="secret",
                placeholder="App api_hash from my.telegram.org/apps", required=True,
            ),
        ),
        is_configured=telegram_is_configured,
        credential_validate=validate_telegram_credentials,
        user_connection=UserConnectionSpec(
            kind="oauth",
            connected=telegram_connected,
            oauth_router=telegram_router,
        ),
        system_skills=(
            SystemSkill(
                id="system:telegram",
                name="Telegram",
                description="Read dialogs, messages, contacts; send_telegram_message action.",
                when_to_load="Load when the user asks about Telegram dialogs or messages.",
                requires="telegram",
                content_builder=_telegram_skill_content,
            ),
        ),
        action_request_handlers=(SendTelegramMessageHandler(),),
        tools=TELEGRAM_TOOLS,
        script_tool_allowlist=TELEGRAM_TOOL_NAMES,
        unprefixed_action_types=frozenset({"send_telegram_message"}),
        post_load=migrate_legacy_credentials,
        on_shutdown=close_all_clients,
    )
