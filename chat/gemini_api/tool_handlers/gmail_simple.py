"""Gmail Simple tool handlers wrapping the /api/gmail-simple/* endpoint
functions as direct dynamic tools.
"""

import json


# ---------------------------------------------------------------------------
# Gmail Simple tool handlers
#
# Wrap the /api/gmail-simple/* endpoint handlers (api/gmail) so the same
# operations are direct dynamic tools. The HTTP routes stay registered in
# quest.py -- sandboxed scripts keep calling them over the local proxy.
# ---------------------------------------------------------------------------

async def _run_gmail_simple_endpoint(coro) -> str:
    """Await a gmail-simple endpoint coroutine and render a tool result.

    Converts the endpoint's native return shapes (PlainTextResponse for the
    markdown message documents, plain dicts for JSON endpoints) to strings,
    and HTTPException -- the endpoints' error channel -- to the structured
    ``{"error": ...}`` JSON the other dynamic tools return.
    """
    from fastapi import HTTPException
    from starlette.responses import Response

    try:
        result = await coro
    except HTTPException as e:
        detail = e.detail
        if isinstance(detail, dict):
            payload = dict(detail)
            payload.setdefault("error", f"gmail_http_{e.status_code}")
        else:
            payload = {"error": f"gmail_http_{e.status_code}", "message": str(detail)}
        return json.dumps(payload)
    except Exception as e:
        return json.dumps({"error": f"Gmail request failed: {e}"})

    if isinstance(result, Response):
        body = result.body
        if isinstance(body, bytes):
            return body.decode("utf-8", errors="replace")
        return str(body)
    if isinstance(result, (dict, list)):
        return json.dumps(result, default=str)
    return str(result)


def _tool_arg_bool(value) -> bool:
    """Coerce a model-supplied boolean-ish tool argument to bool."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


async def _handle_get_gmail_messages(
    user: dict,
    conversation_id: str | None,
    message_ids: list,
    include_html=False,
    include_urls=False,
) -> str:
    """Fetch one or more Gmail messages as a markdown document.

    A single id uses the single-message renderer (no batch chrome); multiple
    ids go through the batch endpoint, which enforces the 50-id cap and keeps
    per-id failures from aborting the batch.
    """
    from api.gmail import get_message_simple, get_messages_batch

    if not isinstance(message_ids, list):
        return json.dumps({"error": "message_ids must be a list of Gmail message IDs."})
    ids = [str(item).strip() for item in message_ids if str(item).strip()]
    if not ids:
        return json.dumps({"error": "message_ids must contain at least one Gmail message ID."})

    include_html = _tool_arg_bool(include_html)
    include_urls = _tool_arg_bool(include_urls)

    if len(ids) == 1:
        coro = get_message_simple(
            ids[0],
            user=user,
            include_html=include_html,
            include_urls=include_urls,
            conversation_id=conversation_id,
        )
    else:
        coro = get_messages_batch(
            ",".join(ids),
            user=user,
            include_html=include_html,
            include_urls=include_urls,
            conversation_id=conversation_id,
        )
    return await _run_gmail_simple_endpoint(coro)


async def _handle_list_gmail_labels(user: dict, label_id=None) -> str:
    """List all Gmail labels, or fetch a single label when label_id is set."""
    from api.gmail import get_label_simple, list_labels_simple

    label_id = str(label_id).strip() if label_id is not None else ""
    if label_id:
        coro = get_label_simple(label_id, user=user)
    else:
        coro = list_labels_simple(user=user)
    return await _run_gmail_simple_endpoint(coro)


async def _handle_get_gmail_message_urls(
    user: dict,
    conversation_id: str | None,
    message_id: str,
    identifiers=None,
) -> str:
    """Resolve (#N#) URL identifiers for a fetched message back to full URLs."""
    from api.gmail import get_urls_by_identifiers, get_urls_for_message

    if not message_id or not str(message_id).strip():
        return json.dumps({"error": "message_id is required."})
    message_id = str(message_id).strip()

    if identifiers:
        if not isinstance(identifiers, list):
            return json.dumps({"error": "identifiers must be a list of numeric identifiers."})
        joined = ",".join(str(item).strip() for item in identifiers)
        coro = get_urls_by_identifiers(
            message_id, joined, user=user, conversation_id=conversation_id,
        )
    else:
        coro = get_urls_for_message(
            message_id, user=user, conversation_id=conversation_id,
        )
    return await _run_gmail_simple_endpoint(coro)


async def _handle_create_gmail_draft(
    user: dict,
    conversation_id: str | None,
    args: dict,
) -> str:
    """Create a Gmail draft from tool arguments.

    Builds the CreateDraftRequest the endpoint expects, with conversation_id
    supplied from dispatch context (never from the model) so workspace
    attachments resolve against the right workspace.
    """
    from pydantic import ValidationError

    from api.gmail import create_draft
    from api.gmail.models import CreateDraftRequest

    known_fields = set(CreateDraftRequest.model_fields) - {"conversation_id"}
    payload = {k: v for k, v in args.items() if k in known_fields}
    unknown = set(args) - known_fields - {"intent_message", "conversation_id"}
    if unknown:
        return json.dumps({
            "error": (
                f"Unknown draft parameter(s): {', '.join(sorted(unknown))}. "
                f"Allowed: {', '.join(sorted(known_fields))}."
            ),
        })

    try:
        draft_request = CreateDraftRequest(
            **payload, conversation_id=conversation_id,
        )
    except ValidationError as e:
        return json.dumps({"error": f"Invalid draft parameters: {e}"})

    return await _run_gmail_simple_endpoint(create_draft(draft_request, user=user))


async def _handle_send_gmail_to_self(
    user: dict,
    subject,
    body_md,
    conversation_id: str | None = None,
    attachments=None,
) -> str:
    """Send a [Quest]-prefixed email from the user to themselves.

    ``conversation_id`` comes from dispatch context (never from the model)
    so workspace attachments resolve against the right workspace.
    """
    from pydantic import ValidationError

    from api.gmail import send_email_to_self
    from api.gmail.models import SendEmailToSelfRequest

    try:
        email_request = SendEmailToSelfRequest(
            subject=subject,
            body_md=body_md,
            attachments=attachments,
            conversation_id=conversation_id,
        )
    except ValidationError as e:
        return json.dumps({"error": f"Invalid parameters: {e}"})

    return await _run_gmail_simple_endpoint(
        send_email_to_self(email_request, user=user)
    )


# The Slack tool handlers (_run_slack_endpoint, the read handlers,
# send_slack_dm_to_self, find_slack_channel) and their argument coercers
# moved to the in-tree Slack plugin (plugins/slack/tools.py).

