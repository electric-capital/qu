"""Raw Gmail API batch request support.

The 7 Gmail Raw GET endpoints (list/get messages, threads, labels,
attachments) have been migrated to the authed_get pattern -- the LLM now
calls the Gmail API directly via tool_call(tool_name="authed_get", ...).

This module retains only the batch POST endpoint which proxies a
multipart/mixed batch request to the Gmail API.
"""

import httpx
from fastapi import HTTPException, Request, Depends
from fastapi.responses import Response

from auth.session import get_current_user
from auth.google_credentials import make_authenticated_request

from .helpers import parse_and_validate_batch


async def batch_request(
    request: Request,
    user: dict = Depends(get_current_user)
):
    """Handle Gmail batch requests.

    Accepts a multipart/mixed batch request containing multiple Gmail API requests.
    Validates that all requests are GET requests to allowed endpoints, then proxies
    the entire batch request to Gmail API.
    """
    body = await request.body()
    body_str = body.decode('utf-8')
    # The same header value is validated against and forwarded upstream so
    # the validator splits the body exactly as Gmail will.
    content_type = request.headers.get("Content-Type", "multipart/mixed")

    # Parse and validate all requests in the batch
    try:
        parse_and_validate_batch(body_str, content_type)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_batch_format",
                "message": f"Failed to parse batch request: {str(e)}"
            }
        )

    # Proxy the entire batch request to Gmail
    batch_url = "https://gmail.googleapis.com/batch/gmail/v1"

    async with httpx.AsyncClient() as client:
        response = await make_authenticated_request(
            client,
            user,
            "POST",
            batch_url,
            content=body,
            headers={"Content-Type": content_type}
        )

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
