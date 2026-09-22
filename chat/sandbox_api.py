"""Dedicated sandbox tool API server for script containers.

The ``run_script`` / ``run_python`` containers used to reach the main Quest
server port, which exposed the entire HTTP surface (auth routes, chat API,
admin endpoints, the inference API, the frontend) to sandboxed code -- the
API key gated most of it, but the reachable surface was far larger than
what scripts legitimately need. This module carves the script-facing
endpoints out onto their own port:

* A second in-process uvicorn server (started from quest.py's lifespan)
  binds ``127.0.0.1:<sandbox port>`` and serves ONLY the endpoints sandbox
  scripts are documented to use: ``POST /api/tool-call``,
  ``POST /api/authed-get`` / ``/api/authed-post``, and the Gmail Simple
  routes. Nothing else exists on that port.
* Script containers get ``QUEST_PORT=<sandbox port>`` injected (see
  ``_build_script_podman_cmd``), so the container entrypoint's socat
  forwarder and iptables rules -- which both key on ``QUEST_PORT`` --
  automatically confine the container to the sandbox port. The main server
  port is REJECTed by the container's own iptables OUTPUT rules.
* Binding to loopback keeps the sandbox port off the LAN entirely;
  containers reach host loopback via slirp4netns's 10.0.2.2 gateway.
* The sandbox app authenticates ONLY ephemeral per-run sandbox tokens
  (chat/sandbox_tokens.py, injected as ``QUEST_API_KEY``): every
  cookie/``users.api_key`` dependency the shared routes declare is
  overridden by ``install_sandbox_auth``, so the user's long-lived key is
  useless on this port and a sandbox token is useless on the main port.

The route roster is registered through ``register_sandbox_api_routes`` on
BOTH the main app (quest.py -- the LLM's in-process ``curl_proxy_*``
dispatch resolves against the main app's routes) and the sandbox app, so
the two surfaces cannot drift.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Generator

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)


def register_sandbox_api_routes(app: FastAPI) -> None:
    """Register the script-facing API routes on *app*.

    Shared by the main Quest app and the sandbox-only app below. Keep this
    the single registration site for these routes: anything added here
    becomes reachable from sandboxed script containers.
    """
    from api import gmail
    from chat.gemini_api.authed_get import authed_get_endpoint, authed_post_endpoint
    from chat.gemini_api.script_tool_call import script_tool_call_endpoint

    # Authed GET/POST proxy endpoints (authenticated external API requests)
    app.post("/api/authed-get")(authed_get_endpoint)
    app.post("/api/authed-post")(authed_post_endpoint)

    # Script tool-call bridge: allow-listed subset of the dynamic tools
    # (Slack reads, Gmail Simple, memory reads) with the same
    # {tool_name, arguments} shape the LLM uses.
    app.post("/api/tool-call")(script_tool_call_endpoint)

    # Gmail Simple API routes
    app.get(
        "/api/gmail-simple/messages/{message_id}",
        response_class=PlainTextResponse,
    )(gmail.get_message_simple)
    app.get(
        "/api/gmail-simple/messages",
        response_class=PlainTextResponse,
    )(gmail.get_messages_batch)
    app.get("/api/gmail-simple/labels")(gmail.list_labels_simple)
    app.get("/api/gmail-simple/labels/{label_id}")(gmail.get_label_simple)

    # Gmail Simple API - URL lookup (for URL-replaced message bodies)
    app.get("/api/gmail-simple/urls/{message_id}/{identifiers}")(gmail.get_urls_by_identifiers)
    app.get("/api/gmail-simple/urls/{message_id}")(gmail.get_urls_for_message)

    # Gmail Simple API - Draft creation
    app.post("/api/gmail-simple/drafts")(gmail.create_draft)

    # Gmail Simple API - Send email to self
    app.post("/api/gmail-simple/send-self")(gmail.send_email_to_self)


def _db_bearer_auth_dependencies() -> tuple:
    """Every auth dependency the shared script-facing routes may declare.

    Each of these accepts the long-lived ``users.api_key`` (and/or the
    session cookie). On the sandbox app all of them are swapped for the
    ephemeral-token-only :func:`get_current_sandbox_user`.
    """
    import auth.session as session_auth
    import chat.auth as chat_auth

    return (
        session_auth.get_current_user,
        session_auth.get_current_user_cookie_or_apikey,
        chat_auth.get_current_user_cookie_or_apikey,
        chat_auth.get_current_user_cookie_or_apikey_checked,
    )


def install_sandbox_auth(app: FastAPI) -> None:
    """Make the sandbox app accept ONLY ephemeral sandbox tokens.

    The route functions are shared with the main app and declare the
    usual cookie/``users.api_key`` dependencies; FastAPI's
    ``dependency_overrides`` swaps every one of them for
    ``get_current_sandbox_user`` (chat/sandbox_tokens.py) on this app
    alone. Net effect: a container's injected ``QUEST_API_KEY`` works
    here and nowhere else, and a leaked ``users.api_key`` works nowhere
    a container can reach.
    """
    from chat.sandbox_tokens import get_current_sandbox_user

    for dep in _db_bearer_auth_dependencies():
        app.dependency_overrides[dep] = get_current_sandbox_user


def create_sandbox_app() -> FastAPI:
    """Build the minimal FastAPI app served on the sandbox port."""
    app = FastAPI(
        title="Quest Sandbox Tool API",
        description="Script-container-facing subset of the Quest API",
        # No interactive docs on the sandbox surface.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    register_sandbox_api_routes(app)
    install_sandbox_auth(app)

    @app.get("/health")
    async def health_check() -> dict:
        return {"status": "ok", "server": "sandbox"}

    return app


class _NoSignalServer(uvicorn.Server):
    """uvicorn.Server that never touches process signal handlers.

    The sandbox server runs as a background task inside the main uvicorn
    process; the stock ``Server.capture_signals`` would steal SIGINT/SIGTERM
    from the primary server when serve() runs on the main thread.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


def start_sandbox_server(port: int):
    """Start the sandbox tool API server on ``127.0.0.1:port``.

    Returns ``(server, task)``; call :func:`stop_sandbox_server` with them
    on shutdown. ``log_config=None`` keeps uvicorn from reconfiguring the
    process-wide logging the main server already set up.
    """
    config = uvicorn.Config(
        create_sandbox_app(),
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
    )
    server = _NoSignalServer(config)
    task = asyncio.create_task(server.serve(), name="sandbox-api-server")

    def _log_failure(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                "Sandbox tool API server on 127.0.0.1:%d failed: %s -- "
                "run_script/run_python API access will be unavailable",
                port, exc,
            )

    task.add_done_callback(_log_failure)
    logger.info("Sandbox tool API server listening on 127.0.0.1:%d", port)
    return server, task


async def stop_sandbox_server(server, task) -> None:
    """Gracefully stop the sandbox server started by start_sandbox_server."""
    server.should_exit = True
    try:
        await asyncio.wait_for(task, timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
