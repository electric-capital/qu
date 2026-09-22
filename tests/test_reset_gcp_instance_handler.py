"""Tests for the reset_gcp_instance action_request handler.

Covers ``ResetGcpInstanceHandler.validate_params`` (unknown-key
rejection, missing/malformed project/zone/instance),
``validate_against_upstream`` (404 same-turn rejection, powered-off
rejection, status/machine-type preview injection, transient-failure
deferral), ``render_preview`` (id rows, injected status, effect
warning) and ``execute`` (connection/scope pre-checks, the reset POST
via a mocked ``make_authenticated_request``, error mapping).
"""

import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


def _handler():
    from chat.action_request_types.reset_gcp_instance import ResetGcpInstanceHandler
    return ResetGcpInstanceHandler()


def _params():
    return {
        "project": "my-project-123",
        "zone": "us-central1-a",
        "instance": "web-server-1",
    }


def _user_with_gcp_scope():
    return {
        "id": 1,
        "google_services_oauth": {
            "access_token": "tok",
            "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
        },
    }


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _patch_request(monkeypatch, fake):
    monkeypatch.setattr(
        "chat.action_request_types.reset_gcp_instance.make_authenticated_request",
        fake,
    )


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


def test_handler_metadata():
    from db.models import ActionRequestType

    handler = _handler()
    assert handler.type_name == ActionRequestType.RESET_GCP_INSTANCE
    assert handler.display_name == "Reset GCP VM"
    assert handler.approve_label == "Reset"
    assert handler.resolved_label == "Reset"


def test_handler_registered():
    import chat.action_request_types  # noqa: F401  (registers handlers)
    from chat.action_request_types.registry import get_handler

    assert get_handler("reset_gcp_instance") is not None


def test_type_in_create_action_request_enum():
    import chat.action_request_types  # noqa: F401
    from chat.llm.tool_schemas import ACTION_REQUEST_TYPE_ENUM

    assert "reset_gcp_instance" in ACTION_REQUEST_TYPE_ENUM


def test_summary_snippet():
    handler = _handler()
    assert handler.summary_snippet(_params()) == (
        "web-server-1 (my-project-123/us-central1-a)"
    )
    # Legacy/odd params never raise.
    assert handler.summary_snippet({}) == ""


# ---------------------------------------------------------------------------
# validate_params
# ---------------------------------------------------------------------------


def test_validate_params_rejects_unknown_field():
    handler = _handler()
    with pytest.raises(ValueError, match="Unknown parameter for reset_gcp_instance"):
        handler.validate_params({**_params(), "force": True})


def test_validate_params_rejects_injected_preview_fields():
    # Server-injected preview fields are not model-suppliable.
    handler = _handler()
    with pytest.raises(ValueError, match="Unknown parameter"):
        handler.validate_params({**_params(), "instance_status": "RUNNING"})


@pytest.mark.parametrize("missing", ["project", "zone", "instance"])
def test_validate_params_rejects_missing_field(missing):
    handler = _handler()
    params = _params()
    del params[missing]
    with pytest.raises(ValueError, match=missing):
        handler.validate_params(params)


@pytest.mark.parametrize(
    "key,value",
    [
        ("project", "Bad_Project"),
        ("project", "p"),
        ("zone", "us central1 a"),
        ("zone", "../zones"),
        ("instance", "UPPER"),
        ("instance", "has/slash"),
        ("instance", 42),
    ],
)
def test_validate_params_rejects_malformed_values(key, value):
    handler = _handler()
    with pytest.raises(ValueError, match=key):
        handler.validate_params({**_params(), key: value})


def test_validate_params_trims_and_passes_through():
    handler = _handler()
    out = handler.validate_params({
        "project": "  my-project-123  ",
        "zone": "us-central1-a",
        "instance": "web-server-1",
    })
    assert out == _params()


def test_validate_params_accepts_domain_scoped_project():
    handler = _handler()
    out = handler.validate_params({**_params(), "project": "example.com:my-proj"})
    assert out["project"] == "example.com:my-proj"


# ---------------------------------------------------------------------------
# validate_against_upstream
# ---------------------------------------------------------------------------


def test_upstream_skips_without_google_connection():
    handler = _handler()
    params = _params()
    out = _run(handler.validate_against_upstream(
        params, {"id": 1, "google_services_oauth": {}}
    ))
    assert out == params
    assert "instance_status" not in out


def test_upstream_rejects_missing_instance(monkeypatch):
    handler = _handler()

    async def _fake(client, user, method, url, **kwargs):
        return _FakeResponse(status_code=404)

    _patch_request(monkeypatch, _fake)
    with pytest.raises(ValueError, match="Instance not found"):
        _run(handler.validate_against_upstream(_params(), _user_with_gcp_scope()))


@pytest.mark.parametrize("status", ["TERMINATED", "SUSPENDED"])
def test_upstream_rejects_powered_off_instance(monkeypatch, status):
    handler = _handler()

    async def _fake(client, user, method, url, **kwargs):
        return _FakeResponse(payload={"name": "web-server-1", "status": status})

    _patch_request(monkeypatch, _fake)
    with pytest.raises(ValueError, match=status):
        _run(handler.validate_against_upstream(_params(), _user_with_gcp_scope()))


def test_upstream_injects_status_and_machine_type(monkeypatch):
    handler = _handler()
    captured: dict = {}

    async def _fake(client, user, method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        return _FakeResponse(payload={
            "name": "web-server-1",
            "status": "RUNNING",
            "machineType": (
                "https://www.googleapis.com/compute/v1/projects/my-project-123/"
                "zones/us-central1-a/machineTypes/e2-medium"
            ),
        })

    _patch_request(monkeypatch, _fake)
    out = _run(handler.validate_against_upstream(_params(), _user_with_gcp_scope()))
    assert out["instance_status"] == "RUNNING"
    assert out["machine_type"] == "e2-medium"
    assert captured["method"] == "GET"
    assert captured["url"].startswith(
        "https://compute.googleapis.com/compute/v1/projects/my-project-123"
        "/zones/us-central1-a/instances/web-server-1"
    )


def test_upstream_defers_on_network_failure(monkeypatch):
    handler = _handler()

    async def _fake(client, user, method, url, **kwargs):
        raise ConnectionError("boom")

    _patch_request(monkeypatch, _fake)
    params = _params()
    out = _run(handler.validate_against_upstream(params, _user_with_gcp_scope()))
    assert out == params


def test_upstream_defers_on_403(monkeypatch):
    # Scope/API-enablement 403s belong to execute(), not Invalid parameters.
    handler = _handler()

    async def _fake(client, user, method, url, **kwargs):
        return _FakeResponse(status_code=403)

    _patch_request(monkeypatch, _fake)
    params = _params()
    out = _run(handler.validate_against_upstream(params, _user_with_gcp_scope()))
    assert out == params


# ---------------------------------------------------------------------------
# render_preview
# ---------------------------------------------------------------------------


def test_render_preview_basic_rows():
    handler = _handler()
    out = _run(handler.render_preview(_params()))
    assert {"key": "Instance", "value": "web-server-1"} in out
    assert {"key": "Zone", "value": "us-central1-a"} in out
    assert {"key": "Project", "value": "my-project-123"} in out
    keys = [row["key"] for row in out]
    assert "Current status" not in keys
    assert "Effect" in keys


def test_render_preview_shows_injected_status():
    handler = _handler()
    out = _run(handler.render_preview({
        **_params(),
        "instance_status": "RUNNING",
        "machine_type": "e2-medium",
    }))
    assert {"key": "Current status", "value": "RUNNING"} in out
    assert {"key": "Machine type", "value": "e2-medium"} in out


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


def test_execute_rejects_when_not_connected():
    handler = _handler()
    with pytest.raises(RuntimeError, match="not connected"):
        _run(handler.execute(_params(), {"id": 1, "google_services_oauth": {}}))


def test_execute_rejects_when_missing_scope():
    handler = _handler()
    user = {
        "id": 1,
        "google_services_oauth": {"access_token": "tok", "scopes": []},
    }
    with pytest.raises(RuntimeError, match="reconnect"):
        _run(handler.execute(_params(), user))


def test_execute_resets_instance(monkeypatch):
    handler = _handler()
    captured: dict = {}

    async def _fake(client, user, method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        return _FakeResponse(payload={
            "name": "operation-12345",
            "status": "RUNNING",
        })

    _patch_request(monkeypatch, _fake)
    result = _run(handler.execute(_params(), _user_with_gcp_scope()))

    assert result["success"] is True
    assert result["instance"] == "web-server-1"
    assert result["operation"] == "operation-12345"
    assert result["operation_status"] == "RUNNING"
    assert captured["method"] == "POST"
    assert captured["url"] == (
        "https://compute.googleapis.com/compute/v1/projects/my-project-123"
        "/zones/us-central1-a/instances/web-server-1/reset"
    )


def test_execute_maps_google_error(monkeypatch):
    handler = _handler()

    async def _fake(client, user, method, url, **kwargs):
        return _FakeResponse(
            status_code=400,
            payload={"error": {"message": "Instance not running"}},
        )

    _patch_request(monkeypatch, _fake)
    with pytest.raises(RuntimeError, match="Instance not running"):
        _run(handler.execute(_params(), _user_with_gcp_scope()))
