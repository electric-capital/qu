"""Tests for combined server+user gating of plugin services.

``get_user_connected_services()`` reports a plugin service as connected
only when the server-level config is present/valid AND the user-level
connection predicate accepts the user's stored credential row -- closing
the "stale skill" hole where a user-side key kept a skill visible after
an admin disabled the integration server-side. (Each plugin's own
suite pins its instance of this gate.)
"""

from unittest.mock import patch

from api.instructions import get_user_connected_services
from config.plugin_types import CredentialField, QuestPlugin, UserConnectionSpec
from config import plugins as plugins_mod


_SCHEMA = (
    CredentialField(key="enabled", label="Enabled", type="bool"),
)


def _plugin(
    pid: str = "acme",
    *,
    with_schema: bool = True,
    with_user_connection: bool = True,
    connected=None,
) -> QuestPlugin:
    return QuestPlugin(
        id=pid,
        label=pid.title(),
        credential_schema=_SCHEMA if with_schema else (),
        is_configured=(lambda config: bool(config.get("enabled"))) if with_schema else None,
        user_connection=UserConnectionSpec(
            kind="api_key",
            connected=connected or (lambda row: bool(row.get("secret"))),
        ) if with_user_connection else None,
    )


def _services(user, plugin, server_config):
    with patch.object(plugins_mod, "_LOADED", [plugin]), \
            patch("config.service_credentials.read_service_credentials",
                  return_value=server_config):
        return get_user_connected_services(user)


_KEYED_USER = {"service_credentials": {"acme": {"service": "acme", "secret": "sk-1"}}}


def test_gating_matrix_configured_x_connected():
    plugin = _plugin()
    # server unconfigured (no store file) x user keyed -> not connected
    assert _services(_KEYED_USER, plugin, None)["acme"] is False
    # server stored-but-disabled x user keyed -> not connected
    assert _services(_KEYED_USER, plugin, {"enabled": False})["acme"] is False
    # server configured x no user row -> not connected
    assert _services({}, plugin, {"enabled": True})["acme"] is False
    # server configured x user keyed -> connected
    assert _services(_KEYED_USER, plugin, {"enabled": True})["acme"] is True


def test_plugin_without_user_connection_gates_on_server_only():
    plugin = _plugin(with_user_connection=False)
    assert _services({}, plugin, {"enabled": True})["acme"] is True
    assert _services({}, plugin, {"enabled": False})["acme"] is False


def test_plugin_without_schema_or_user_connection_is_always_available():
    plugin = _plugin(with_schema=False, with_user_connection=False)
    assert _services({}, plugin, None)["acme"] is True


def test_raising_predicates_fail_closed():
    def _boom(_row):
        raise RuntimeError("boom")

    plugin = _plugin(connected=_boom)
    assert _services(_KEYED_USER, plugin, {"enabled": True})["acme"] is False

    raising_configured = QuestPlugin(
        id="acme", label="Acme",
        credential_schema=_SCHEMA,
        is_configured=_boom,
        user_connection=UserConnectionSpec(
            kind="api_key", connected=lambda row: True,
        ),
    )
    assert _services(_KEYED_USER, raising_configured, {"enabled": True})["acme"] is False
