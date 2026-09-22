"""Pydantic request/response models for Gmail API endpoints."""

from typing import Optional, List, Literal

from pydantic import BaseModel


class DraftAttachment(BaseModel):
    """A single attachment for a draft email."""
    type: Literal["drive", "workspace", "gmail"]
    drive_file_id: Optional[str] = None
    workspace_path: Optional[str] = None
    message_id: Optional[str] = None
    attachment_id: Optional[str] = None
    filename: Optional[str] = None


class CreateDraftRequest(BaseModel):
    """Request body for creating a Gmail draft."""
    to: str  # Recipient email address(es), comma-separated
    subject: str  # Email subject line
    body: Optional[str] = None  # Plain text email body (required if body_md is not provided)
    body_md: Optional[str] = None  # Markdown-formatted email body; rendered to HTML for the email. If body is also provided, body is used as the plain text part; otherwise raw markdown is the plain text fallback
    cc: Optional[str] = None
    bcc: Optional[str] = None
    in_reply_to_message_id: Optional[str] = None  # Message-ID header of message being replied to
    references: Optional[str] = None  # References header value
    thread_id: Optional[str] = None  # Gmail thread ID for threading
    attachments: Optional[List[DraftAttachment]] = None
    conversation_id: Optional[str] = None


class SendEmailToSelfRequest(BaseModel):
    """Request body for sending an email to yourself."""
    subject: str  # Email subject line (will be prefixed with [Quest])
    body_md: str  # Markdown-formatted email body (rendered to HTML for the email)
    attachments: Optional[List[DraftAttachment]] = None  # Same shapes as drafts; images embed via ![](cid:<filename>)
    conversation_id: Optional[str] = None  # Injected by dispatch; required for workspace attachments
