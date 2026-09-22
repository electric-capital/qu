"""``_attach_file`` sends a filename-derived Graph ``contentId`` (and
``isInline`` for images) on both the simple-POST and upload-session shapes,
so ``body_md`` can embed an attached image as ``![caption](cid:<token>)``.
"""

import asyncio
import base64
import json

import plugins.m365.tools as tools_mod
from plugins.m365.tools import _attach_file, _attachment_summary


class _Resp:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def test_attachment_summary_derives_content_id():
    assert _attachment_summary(
        {"filename": "my chart.png", "mime_type": "image/png", "data": b""}
    ) == {"filename": "my chart.png", "content_id": "my_chart.png", "inline_image": True}
    assert _attachment_summary(
        {"filename": "results.csv", "mime_type": "text/csv", "data": b""}
    )["inline_image"] is False


def test_simple_post_carries_content_id_and_inline_flag(monkeypatch):
    calls = []

    async def fake_graph_request(user, method, url, **kwargs):
        calls.append((method, url, kwargs.get("json_body")))
        return _Resp()

    monkeypatch.setattr(tools_mod, "graph_request", fake_graph_request)
    png = b"\x89PNG" + b"\x00" * 8
    asyncio.run(_attach_file(
        {"id": 1}, "DRAFT1",
        {"filename": "my chart.png", "mime_type": "image/png", "data": png},
    ))
    assert len(calls) == 1
    method, url, body = calls[0]
    assert method == "POST" and url.endswith("/me/messages/DRAFT1/attachments")
    assert body["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert body["name"] == "my chart.png"
    assert body["contentId"] == "my_chart.png"
    assert body["isInline"] is True
    assert body["contentBytes"] == base64.b64encode(png).decode("ascii")


def test_simple_post_non_image_is_not_inline(monkeypatch):
    calls = []

    async def fake_graph_request(user, method, url, **kwargs):
        calls.append(kwargs.get("json_body"))
        return _Resp()

    monkeypatch.setattr(tools_mod, "graph_request", fake_graph_request)
    asyncio.run(_attach_file(
        {"id": 1}, "DRAFT1",
        {"filename": "results.csv", "mime_type": "text/csv", "data": b"a,b\n"},
    ))
    assert calls[0]["contentId"] == "results.csv"
    assert calls[0]["isInline"] is False


def test_upload_session_item_carries_content_id_and_inline_flag(monkeypatch):
    session_bodies = []
    put_calls = []

    async def fake_graph_request(user, method, url, **kwargs):
        assert url.endswith("/attachments/createUploadSession")
        session_bodies.append(kwargs.get("json_body"))
        return _Resp(201, {"uploadUrl": "https://upload.example/session"})

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def put(self, url, content=None, headers=None):
            put_calls.append((url, len(content), headers))
            return _Resp(202)

    monkeypatch.setattr(tools_mod, "graph_request", fake_graph_request)
    monkeypatch.setattr(tools_mod.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(tools_mod, "_SIMPLE_ATTACHMENT_LIMIT", 4)

    data = b"\xff\xd8" + b"\x00" * 30
    asyncio.run(_attach_file(
        {"id": 1}, "DRAFT2",
        {"filename": "big photo.jpg", "mime_type": "image/jpeg", "data": data},
    ))
    item = session_bodies[0]["AttachmentItem"]
    assert item["name"] == "big photo.jpg"
    assert item["contentId"] == "big_photo.jpg"
    assert item["isInline"] is True
    assert item["size"] == len(data)
    assert put_calls and all(h["Content-Type"] == "application/octet-stream" for _, _, h in put_calls)
    assert sum(n for _, n, _ in put_calls) == len(data)
