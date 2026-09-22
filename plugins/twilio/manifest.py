"""The Twilio (SMS) integration plugin manifest.

Plugin id ``twilio``: the ``connected_services`` key, the ``system:twilio``
skill gate, the admin credential store file (``twilio.json``: Account
SID, Auth Token, sending number / Messaging Service SID, and the
**trusted channel** switch), and the router namespace (``/auth/twilio``).

Two things differ from the other in-tree plugins:

1. **No OAuth.** The per-user connection is a verified phone number: the
   user types it, Quest texts a code, the user types the code back
   (plugins/twilio/verify.py). The connection is still declared as the
   ``oauth`` kind because that is the manifest shape that mounts a
   plugin-owned, session-cookie-authed router under ``/auth/<id>`` and
   renders the generic Connect-popup row; the popup page is the plugin's
   own verification form rather than a provider redirect.
2. **Trusted-channel policy.** Texts to *other* people always ride on the
   approval-gated ``twilio_send_sms`` action request. Texts to the user's
   own number use the no-approval ``twilio_send_self_sms`` tool, and the
   admin's ``trusted_channel`` checkbox on the Service Credentials card
   decides whether that tool may send free-form text or only one of the
   user's pre-written messages (Settings > SMS Messages).
"""

from pathlib import Path

from chat.system_skills import SystemSkill
from config.plugin_types import CredentialField, QuestPlugin, UserConnectionSpec

from plugins.twilio.handlers import TwilioSendSmsHandler
from plugins.twilio.tools import TWILIO_TOOLS
from plugins.twilio.upstream import (
    twilio_connected,
    twilio_is_configured,
    validate_twilio_credentials,
)
from plugins.twilio.verify import router as twilio_router

_PLUGIN_DIR = Path(__file__).parent


def _twilio_skill_content(_base_url: str, _api_key: str) -> str:
    """The system:twilio skill body, from the instructions.md data file."""
    return (_PLUGIN_DIR / "instructions.md").read_text()


def get_plugin() -> QuestPlugin:
    return QuestPlugin(
        id="twilio",
        label="Twilio SMS",
        credential_schema=(
            CredentialField(
                key="account_sid", label="Account SID", type="text",
                placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                required=True,
            ),
            CredentialField(
                key="auth_token", label="Auth Token", type="secret",
                placeholder="Twilio Auth Token",
                required=True,
            ),
            CredentialField(
                key="from_number", label="Sending number or Messaging Service SID",
                type="text",
                placeholder="+15551234567 or MGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                required=True,
            ),
            CredentialField(
                key="trusted_channel",
                label=(
                    "Trusted channel -- allow free-form texts to a user's own "
                    "verified number without approval (off: only the user's "
                    "pre-written SMS messages can be sent)"
                ),
                type="bool",
            ),
        ),
        is_configured=twilio_is_configured,
        credential_validate=validate_twilio_credentials,
        user_connection=UserConnectionSpec(
            kind="oauth",
            connected=twilio_connected,
            oauth_router=twilio_router,
        ),
        system_skills=(
            SystemSkill(
                id="system:twilio",
                name="Twilio SMS",
                description=(
                    "Text the user (twilio_send_self_sms, no approval) or "
                    "others (twilio_send_sms action request)."
                ),
                when_to_load=(
                    "Load when the user asks to be texted / sent an SMS, or "
                    "to text someone else."
                ),
                requires="twilio",
                content_builder=_twilio_skill_content,
            ),
        ),
        action_request_handlers=(TwilioSendSmsHandler(),),
        tools=TWILIO_TOOLS,
    )
