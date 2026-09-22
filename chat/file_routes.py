"""REST API endpoints for file browser functionality."""

import asyncio
import json
import logging
import os
import uuid

import httpx
from fastapi import HTTPException, Depends, APIRouter, UploadFile, File, Query, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from typing import List, Optional
from chat.realtime import bus, events as realtime_events
from chat.conversation_access import resolve_owned_workspace
from chat.storage import ChatStorage
from chat.auth import get_current_user_cookie_or_apikey_checked
from chat.file_storage import (
    list_workspace_files,
    save_uploaded_file,
    save_uploaded_file_with_path,
    get_file_download,
    get_file_content,
    create_folder_zip,
    delete_workspace_item,
    count_workspace_item_files,
    create_workspace_folder,
    MAX_FILE_SIZE
)
from auth.google_credentials import make_authenticated_request


# Composer paste attachment configuration.
# Only PNG and JPEG are accepted; other clipboard image types (gif/webp/svg)
# are rejected up front. Magic-byte sniffing is performed in addition to the
# content_type check so a tampered request can't smuggle non-image bytes.
_COMPOSER_ATTACHMENT_ALLOWED_MIMES = frozenset({"image/png", "image/jpeg"})
# 10 MB per image: gives some headroom over Anthropic's 5 MB limit (the
# provider returns None for oversized images and the conversation loop
# substitutes a text note). Gemini accepts larger files.
_COMPOSER_ATTACHMENT_MAX_SIZE = 10 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


def _sniff_image_mime(data: bytes) -> Optional[str]:
    """Return the inferred image MIME by magic bytes, or None if unknown."""
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    return None


# Raster image types served inline with their real MIME by the download
# endpoint, so the chat UI can embed workspace images directly via
# <img src=".../files/download?path=...."> (inline markdown images and
# composer-attachment thumbnails). SVG is deliberately NOT here: served
# inline as image/svg+xml, a workspace SVG opened directly would execute
# its scripts on the app origin. Everything else stays a generic
# octet-stream attachment download.
_INLINE_IMAGE_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

logger = logging.getLogger(__name__)

DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


def _publish_file_list_changed(
    user_id: int, conversation_id: str, project_id: Optional[str],
) -> None:
    """Best-effort: publish ``file_list_changed`` so file browsers in any
    tab (same conversation, or sibling conversations under the same project)
    silent-refresh. Project-scoped writes fan out across sibling conversations
    via the ``scope === "project"`` filter on the FE.
    """
    try:
        scope = "project" if project_id else "conversation"
        bus.publish_to_user(
            user_id,
            realtime_events.make_file_list_changed(
                conversation_id=conversation_id,
                project_id=project_id,
                scope=scope,
            ),
        )
    except Exception:
        logger.debug(
            "[file_routes] publish file_list_changed failed "
            "(user_id=%s, conversation_id=%s)",
            user_id, conversation_id, exc_info=True,
        )

# Create APIRouter for file endpoints
router = APIRouter(
    prefix="/app/api",
    tags=["files"]
)


@router.get("/conversations/{conversation_id}/files")
async def list_files(
    conversation_id: str,
    path: str = Query(default="", description="Relative path within workspace"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked)
):
    """List files in workspace directory.

    Args:
        conversation_id: UUID of the conversation
        path: Relative path within workspace (empty for root)
        user: Authenticated user dictionary

    Returns:
        Dictionary with currentPath, files list, and canGoUp boolean

    Raises:
        HTTPException: 404 if conversation not found, 400 if invalid path
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    try:
        result = await asyncio.to_thread(list_workspace_files, workspace_path, path)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": str(e)
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": str(e)
            }
        )


@router.post("/conversations/{conversation_id}/files/upload")
async def upload_files(
    conversation_id: str,
    files: List[UploadFile] = File(...),
    paths: Optional[List[str]] = Form(None),
    path: str = Query(default="", description="Destination path within workspace"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked)
):
    """Upload files to workspace.

    Args:
        conversation_id: UUID of the conversation
        files: List of files to upload
        path: Destination path within workspace (empty for root)
        user: Authenticated user dictionary

    Returns:
        Dictionary with uploadedFiles and errors lists

    Raises:
        HTTPException: 404 if conversation not found
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    uploaded_files = []
    errors = []

    for i, file in enumerate(files):
        try:
            # Read file content
            content = await file.read()

            # Check file size
            if len(content) > MAX_FILE_SIZE:
                errors.append({
                    "filename": file.filename or "unknown",
                    "error": "file_too_large",
                    "message": f"File exceeds maximum size of {MAX_FILE_SIZE // (1024 * 1024)}MB"
                })
                continue

            # Determine the save path for this file
            if paths and i < len(paths) and "/" in paths[i]:
                # Folder upload: paths[i] contains relative path like "folder/sub/file.txt"
                result = await save_uploaded_file_with_path(
                    workspace_path,
                    paths[i],
                    content,
                    path  # base destination path
                )
            else:
                # Flat upload: use existing behavior
                result = await save_uploaded_file(
                    workspace_path,
                    file.filename or "unnamed_file",
                    content,
                    path
                )
            uploaded_files.append(result)

        except ValueError as e:
            # Use the relative path when available (more informative for folder uploads)
            err_filename = (paths[i] if paths and i < len(paths) else None) or file.filename or "unknown"
            errors.append({
                "filename": err_filename,
                "error": "invalid_file",
                "message": str(e)
            })
        except Exception as e:
            err_filename = (paths[i] if paths and i < len(paths) else None) or file.filename or "unknown"
            errors.append({
                "filename": err_filename,
                "error": "upload_failed",
                "message": str(e)
            })

    if uploaded_files:
        _publish_file_list_changed(
            user_id, conversation_id, meta.get("project_id"),
        )

    return {
        "uploadedFiles": uploaded_files,
        "errors": errors
    }


@router.post("/conversations/{conversation_id}/composer-attachments")
async def upload_composer_attachments(
    conversation_id: str,
    files: List[UploadFile] = File(...),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Persist images pasted into the chat composer.

    Accepts PNG/JPEG only and stores each file under
    ``workspace/pasted/<attachment_id>.<ext>`` so the on-disk layout is
    distinct from the user-managed workspace root. Returns the array of
    refs the composer will pass with the next ``send_message`` envelope.

    The endpoint is intentionally stricter than ``/files/upload``: it
    rejects non-image content_types, validates magic bytes, and caps the
    per-file size at 10 MB. Anthropic's 5 MB image limit is enforced
    later in ``anthropic_provider.upload_file`` -- larger files still
    land on disk and the LLM handoff degrades to a text note.
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    attachments: list[dict] = []
    errors: list[dict] = []

    for upload in files:
        original_name = upload.filename or "pasted-image"
        try:
            content = await upload.read()
        except Exception as exc:
            errors.append({
                "filename": original_name,
                "error": "read_failed",
                "message": str(exc),
            })
            continue

        if len(content) == 0:
            errors.append({
                "filename": original_name,
                "error": "empty_file",
                "message": "Empty file",
            })
            continue

        if len(content) > _COMPOSER_ATTACHMENT_MAX_SIZE:
            errors.append({
                "filename": original_name,
                "error": "file_too_large",
                "message": (
                    f"Image exceeds maximum size of "
                    f"{_COMPOSER_ATTACHMENT_MAX_SIZE // (1024 * 1024)}MB"
                ),
            })
            continue

        declared_mime = (upload.content_type or "").lower()
        sniffed_mime = _sniff_image_mime(content)
        # Trust the sniffed MIME when present; fall back to declared content
        # type only when sniffing is inconclusive (defends against a hostile
        # client lying about content_type).
        mime_type = sniffed_mime or declared_mime
        if mime_type not in _COMPOSER_ATTACHMENT_ALLOWED_MIMES:
            errors.append({
                "filename": original_name,
                "error": "unsupported_mime",
                "message": "Only PNG and JPEG images are accepted",
            })
            continue
        if sniffed_mime is None or (
            declared_mime
            and declared_mime in _COMPOSER_ATTACHMENT_ALLOWED_MIMES
            and declared_mime != sniffed_mime
        ):
            # Mismatch between declared content_type and the magic-byte
            # sniff: reject so a tampered request can't pass off arbitrary
            # bytes as a JPEG/PNG. (A missing content_type is OK as long as
            # the magic bytes are valid.)
            errors.append({
                "filename": original_name,
                "error": "mime_mismatch",
                "message": "File contents do not match a PNG or JPEG image",
            })
            continue

        ext = "png" if mime_type == "image/png" else "jpg"
        attachment_id = uuid.uuid4().hex
        stored_filename = f"{attachment_id}.{ext}"

        try:
            saved = await save_uploaded_file(
                workspace_path,
                stored_filename,
                content,
                "pasted",
            )
        except ValueError as exc:
            errors.append({
                "filename": original_name,
                "error": "invalid_file",
                "message": str(exc),
            })
            continue
        except Exception as exc:
            errors.append({
                "filename": original_name,
                "error": "save_failed",
                "message": str(exc),
            })
            continue

        # ``saved["path"]`` is "/pasted/<attachment_id>.<ext>"; strip the
        # leading slash for the workspace-relative ref carried on the WS
        # envelope and the persisted message row.
        workspace_rel = saved["path"].lstrip("/")
        attachments.append({
            "attachment_id": attachment_id,
            "filename": stored_filename,
            "workspace_path": workspace_rel,
            "mime_type": mime_type,
            "size_bytes": saved["size"],
        })

    if attachments:
        _publish_file_list_changed(
            user_id, conversation_id, meta.get("project_id"),
        )

    return {
        "attachments": attachments,
        "errors": errors,
    }


@router.get("/conversations/{conversation_id}/files/content")
async def read_file_content(
    conversation_id: str,
    path: str = Query(..., description="File path within workspace"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked)
):
    """Read text content of a file from workspace.

    Args:
        conversation_id: UUID of the conversation
        path: Path to the file within workspace
        user: Authenticated user dictionary

    Returns:
        Dictionary with name, path, content, and size

    Raises:
        HTTPException: 404 if conversation or file not found, 400 if invalid path or unsupported type
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    try:
        content, filename, size = await asyncio.to_thread(
            get_file_content, workspace_path, path
        )
        return {
            "name": filename,
            "path": path,
            "content": content,
            "size": size
        }
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": str(e)
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": str(e)
            }
        )


@router.get("/conversations/{conversation_id}/files/download")
async def download_file(
    conversation_id: str,
    path: str = Query(..., description="File path within workspace"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked)
):
    """Download a file from workspace.

    Args:
        conversation_id: UUID of the conversation
        path: Path to the file within workspace
        user: Authenticated user dictionary

    Returns:
        FileResponse for download

    Raises:
        HTTPException: 404 if conversation or file not found, 400 if invalid path
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    try:
        file_path, filename = get_file_download(workspace_path, path)
        inline_mime = _INLINE_IMAGE_MIMES.get(os.path.splitext(filename)[1].lower())
        if inline_mime:
            return FileResponse(
                path=file_path,
                filename=filename,
                media_type=inline_mime,
                content_disposition_type="inline",
            )
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type="application/octet-stream"
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": str(e)
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": str(e)
            }
        )


@router.get("/conversations/{conversation_id}/files/download-folder")
async def download_folder_as_zip(
    conversation_id: str,
    path: str = Query(..., description="Folder path within workspace"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked)
):
    """Download a folder from workspace as a zip archive.

    Args:
        conversation_id: UUID of the conversation
        path: Path to the folder within workspace
        user: Authenticated user dictionary

    Returns:
        FileResponse with zip archive for download

    Raises:
        HTTPException: 404 if conversation or folder not found, 400 if invalid path,
                       500 if zip creation fails
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    try:
        temp_zip_path, folder_name = await asyncio.to_thread(
            create_folder_zip, workspace_path, path
        )
        return FileResponse(
            path=temp_zip_path,
            filename=f"{folder_name}.zip",
            media_type="application/zip",
            background=BackgroundTask(os.unlink, temp_zip_path),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": str(e)
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": str(e)
            }
        )
    except OSError as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "zip_creation_failed",
                "message": str(e)
            }
        )


@router.get("/conversations/{conversation_id}/files/info")
async def file_info(
    conversation_id: str,
    path: str = Query(..., description="File or folder path within workspace"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked)
):
    """Get info about a file or folder (name, type, file count).

    Args:
        conversation_id: UUID of the conversation
        path: Path to the file or folder within workspace
        user: Authenticated user dictionary

    Returns:
        Dictionary with name, type, and fileCount

    Raises:
        HTTPException: 404 if conversation or path not found, 400 if invalid path
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    try:
        result = await asyncio.to_thread(
            count_workspace_item_files, workspace_path, path
        )
        return {
            "name": result["name"],
            "type": result["type"],
            "fileCount": result["count"],
        }
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": str(e)
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": str(e)
            }
        )


@router.delete("/conversations/{conversation_id}/files")
async def delete_file(
    conversation_id: str,
    path: str = Query(..., description="File or folder path within workspace to delete"),
    user: dict = Depends(get_current_user_cookie_or_apikey_checked)
):
    """Delete a file or folder from workspace.

    Args:
        conversation_id: UUID of the conversation
        path: Path to the file or folder within workspace
        user: Authenticated user dictionary

    Returns:
        Dictionary with name, type, and deletedCount

    Raises:
        HTTPException: 404 if conversation or path not found, 400 if invalid path
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    try:
        result = await asyncio.to_thread(delete_workspace_item, workspace_path, path)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": str(e)
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": str(e)
            }
        )

    _publish_file_list_changed(
        user_id, conversation_id, meta.get("project_id"),
    )

    return {
        "name": result["name"],
        "type": result["type"],
        "deletedCount": result["deleted_count"],
    }


class CreateFolderRequest(BaseModel):
    """Request body for creating a new folder in the workspace."""
    path: str  # parent directory (relative, "" or "/" for workspace root)
    name: str  # folder name to create (no slashes, no ..)


@router.post("/conversations/{conversation_id}/files/create-folder")
async def create_folder(
    conversation_id: str,
    body: CreateFolderRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked),
):
    """Create a new empty folder inside the workspace.

    Args:
        conversation_id: UUID of the conversation
        body: Request body with parent path and folder name
        user: Authenticated user dictionary

    Returns:
        Dictionary with the created folder's name and full relative path

    Raises:
        HTTPException: 404 if conversation or parent directory not found,
                       400 if invalid path or name
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    try:
        result = create_workspace_folder(workspace_path, body.path, body.name)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": str(e)
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": str(e)
            }
        )

    _publish_file_list_changed(
        user_id, conversation_id, meta.get("project_id"),
    )

    return {
        "name": result["name"],
        "path": result["path"],
    }


class SaveToDriveRequest(BaseModel):
    """Request body for saving a workspace file to Google Drive."""
    path: str
    title: str


@router.post("/conversations/{conversation_id}/files/save-to-drive")
async def save_file_to_drive(
    conversation_id: str,
    body: SaveToDriveRequest,
    user: dict = Depends(get_current_user_cookie_or_apikey_checked)
):
    """Save a workspace markdown file to Google Drive as a Google Doc.

    Reads the raw markdown from the workspace and uploads it to Google Drive,
    which natively converts markdown to a Google Document.

    Args:
        conversation_id: UUID of the conversation
        body: Request body with path and title
        user: Authenticated user dictionary

    Returns:
        Dictionary with id, name, and url of the created Google Doc

    Raises:
        HTTPException: 404 if conversation or file not found, 400 if invalid path
    """
    user_id = user["id"]

    meta, workspace_path = await resolve_owned_workspace(user_id, conversation_id)

    try:
        content, filename, size = get_file_content(workspace_path, body.path)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_path",
                "message": str(e)
            }
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "not_found",
                "message": str(e)
            }
        )

    # Upload markdown to Google Drive via multipart upload.
    # Drive natively converts markdown to Google Docs format on import.
    boundary = "----quest_doc_import_boundary"
    metadata = json.dumps({
        "name": body.title,
        "mimeType": "application/vnd.google-apps.document",
    })
    multipart_body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: text/markdown; charset=UTF-8\r\n\r\n"
        f"{content}\r\n"
        f"--{boundary}--"
    )

    url = f"{DRIVE_UPLOAD_BASE}/files?uploadType=multipart"

    async with httpx.AsyncClient() as client:
        response = await make_authenticated_request(
            client,
            user,
            "POST",
            url,
            content=multipart_body.encode("utf-8"),
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )

    data = response.json()

    if not response.is_success:
        error_msg = data.get("error", {}).get("message", "Drive API error")
        raise HTTPException(status_code=response.status_code, detail=error_msg)

    doc_id = data.get("id", "")
    doc_name = data.get("name", body.title)
    return {
        "id": doc_id,
        "name": doc_name,
        "url": f"https://docs.google.com/document/d/{doc_id}/edit",
    }
