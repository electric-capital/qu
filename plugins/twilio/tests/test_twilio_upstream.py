"""Unit tests for the Twilio plugin's upstream helpers (plugins/twilio/upstream.py).

Pure-function coverage plus a mocked-httpx ``send_sms``: admin config
predicates and normalization, the trusted-channel switch, E.164 phone
normalization/masking, the per-user connection predicates, and the
Messages API call shape / error conversion.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import plugins.twilio.upstream as upstream
from plugins.twilio.upstream import (
    MAX_SMS_BODY_LENGTH,
    TwilioError,
    get_user_phone_number,
    is_trusted_channel,
    mask_phone_number,
    normalize_phone_number,
    send_sms,
    twilio_connected,
    twilio_is_configured,
    validate_twilio_credentials,
)


def _run(coro):
    return asyncio.run(coro)


_SID = "AC" + "a" * 32
_CONFIG = {"account_sid": _SID, "auth_token": "tok", "from_number": "+15550001111"}


class TestAdminConfig:
    def test_is_configured_requires_all_three(self):
        assert twilio_is_configured(_CONFIG)
        for missing in ("account_sid", "auth_token", "from_number"):
            assert not twilio_is_configured({**_CONFIG, missing: ""})
        assert not twilio_is_configured({})

    def test_trusted_channel_only_on_json_true(self):
        assert is_trusted_channel({**_CONFIG, "trusted_channel": True})
        assert not is_trusted_channel({**_CONFIG, "trusted_channel": False})
        assert not is_trusted_channel({**_CONFIG, "trusted_channel": "true"})
        assert not is_trusted_channel({**_CONFIG, "trusted_channel": 1})
        assert not is_trusted_channel(_CONFIG)
        assert not is_trusted_channel(None)

    def test_validate_strips_and_normalizes_sender(self):
        out = validate_twilio_credentials({
            "account_sid": f"  {_SID} ",
            "auth_token": "t\n",
            "from_number": " +1 (555) 000-1111 ",
            "trusted_channel": True,
        })
        assert out == {
            "account_sid": _SID,
            "auth_token": "t",
            "from_number": "+15550001111",
            "trusted_channel": True,
        }

    def test_validate_accepts_messaging_service_sid(self):
        mg = "MG" + "b" * 32
        out = validate_twilio_credentials({**_CONFIG, "from_number": mg})
        assert out["from_number"] == mg

    def test_validate_rejects_bad_account_sid(self):
        with pytest.raises(ValueError, match="Account SID"):
            validate_twilio_credentials({**_CONFIG, "account_sid": "not-a-sid"})

    def test_validate_rejects_bad_sender(self):
        with pytest.raises(ValueError, match="Sending number"):
            validate_twilio_credentials({**_CONFIG, "from_number": "5551234"})

    def test_validate_leaves_blank_secret_alone(self):
        # Empty secret = "keep stored" for the generic PUT endpoint.
        out = validate_twilio_credentials({**_CONFIG, "auth_token": ""})
        assert out["auth_token"] == ""


class TestPhoneNumbers:
    @pytest.mark.parametrize("raw,expected", [
        ("+15551234567", "+15551234567"),
        (" +1 (555) 123-4567 ", "+15551234567"),
        ("+44.20.7946.0958", "+442079460958"),
        ("0015551234567", "+15551234567"),
    ])
    def test_normalizes_to_e164(self, raw, expected):
        assert normalize_phone_number(raw) == expected

    @pytest.mark.parametrize("raw", [
        "5551234567",         # no country code -- never guessed
        "(555) 123-4567",
        "+0155512345",        # leading zero country code
        "+1",                 # too short
        "+1234567890123456",  # 16 digits
        "+1555abc4567",
        "",
        None,
        12345,
    ])
    def test_rejects_non_e164(self, raw):
        with pytest.raises(ValueError):
            normalize_phone_number(raw)

    def test_mask_keeps_last_four(self):
        assert mask_phone_number("+15551234567") == "+•••••••4567"
        assert mask_phone_number("") == ""
        assert mask_phone_number(None) == ""


class TestConnection:
    def test_connected_requires_verified_number(self):
        assert twilio_connected({"oauth_blob": {"phone_number": "+15551234567"}})
        # A pending verification alone never counts as connected.
        assert not twilio_connected({"oauth_blob": {"pending": {"phone_number": "+15551234567"}}})
        assert not twilio_connected({"oauth_blob": {}})
        assert not twilio_connected({"oauth_blob": None})
        assert not twilio_connected({})

    def test_get_user_phone_number(self):
        user = {"service_credentials": {"twilio": {"oauth_blob": {"phone_number": "+15551234567"}}}}
        assert get_user_phone_number(user) == "+15551234567"
        assert get_user_phone_number({}) is None
        assert get_user_phone_number({"service_credentials": {"twilio": {"oauth_blob": {"phone_number": ""}}}}) is None


def _patch_http(status_code: int, payload):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=payload)
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return patch.object(upstream.httpx, "AsyncClient", return_value=ctx), client


class TestSendSms:
    def test_posts_messages_resource_with_from_number(self):
        http_patch, client = _patch_http(201, {"sid": "SM1", "status": "queued"})
        with http_patch:
            out = _run(send_sms("+15551234567", "hi", _CONFIG))
        assert out == {"sid": "SM1", "status": "queued"}
        args, kwargs = client.post.await_args
        assert args[0] == f"https://api.twilio.com/2010-04-01/Accounts/{_SID}/Messages.json"
        assert kwargs["data"] == {"To": "+15551234567", "Body": "hi", "From": "+15550001111"}
        assert kwargs["auth"] == (_SID, "tok")

    def test_messaging_service_sid_sender(self):
        mg = "MG" + "c" * 32
        http_patch, client = _patch_http(201, {"sid": "SM2"})
        with http_patch:
            _run(send_sms("+15551234567", "hi", {**_CONFIG, "from_number": mg}))
        _, kwargs = client.post.await_args
        assert kwargs["data"]["MessagingServiceSid"] == mg
        assert "From" not in kwargs["data"]

    def test_twilio_error_message_surfaced(self):
        http_patch, _ = _patch_http(400, {"code": 21211, "message": "Invalid 'To' Phone Number"})
        with http_patch, pytest.raises(TwilioError, match="Invalid 'To' Phone Number \\(Twilio error 21211\\)"):
            _run(send_sms("+15551234567", "hi", _CONFIG))

    def test_body_length_enforced_before_network(self):
        http_patch, client = _patch_http(201, {})
        with http_patch, pytest.raises(TwilioError, match="1-1600"):
            _run(send_sms("+15551234567", "x" * (MAX_SMS_BODY_LENGTH + 1), _CONFIG))
        client.post.assert_not_awaited()

    def test_transport_error_wrapped(self):
        client = MagicMock()
        client.post = AsyncMock(side_effect=upstream.httpx.ConnectError("boom"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=None)
        with patch.object(upstream.httpx, "AsyncClient", return_value=ctx), \
                pytest.raises(TwilioError, match="request failed"):
            _run(send_sms("+15551234567", "hi", _CONFIG))
