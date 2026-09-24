"""Outgoing email over SMTP, used by the email/password sign-in flow.

Configured by an admin in Settings > Service Credentials > "Outgoing email
(SMTP)" (store service ``smtp``, spec in config/service_specs.py). Optional:
without it, password resets and invites go through admin-issued links only.
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20


class MailerError(Exception):
    """Sending failed (misconfiguration or SMTP-level error)."""


def load_smtp_config() -> dict | None:
    """The stored SMTP config when it is usable (host + from address), else None."""
    from config.service_credentials import read_service_credentials

    config = read_service_credentials("smtp")
    if not config or not config.get("host") or not config.get("from_address"):
        return None
    return config


def smtp_configured() -> bool:
    return load_smtp_config() is not None


def _send_sync(config: dict, message: EmailMessage) -> None:
    host = config["host"]
    implicit_tls = bool(config.get("implicit_tls"))
    port = int(config.get("port") or (465 if implicit_tls else 587))
    username = config.get("username") or ""
    password = config.get("password") or ""
    context = ssl.create_default_context()

    if implicit_tls:
        server = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT_SECONDS, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=_TIMEOUT_SECONDS)
    try:
        server.ehlo()
        if not implicit_tls:
            if server.has_extn("starttls"):
                server.starttls(context=context)
                server.ehlo()
            elif username:
                # Never send credentials over an unencrypted connection.
                raise MailerError(
                    f"SMTP server {host}:{port} does not offer STARTTLS; refusing "
                    "to send the password in cleartext. Enable implicit TLS or "
                    "use a port that supports STARTTLS."
                )
        if username:
            server.login(username, password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            pass


async def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Raises MailerError on any failure."""
    config = load_smtp_config()
    if config is None:
        raise MailerError("Outgoing email (SMTP) is not configured.")
    message = EmailMessage()
    message["From"] = config["from_address"]
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        await asyncio.to_thread(_send_sync, config, message)
    except MailerError:
        raise
    except (OSError, smtplib.SMTPException) as exc:
        raise MailerError(f"Sending email failed: {exc}") from exc
    logger.info("[Mailer] Sent %r to %s", subject, parseaddr(to)[1])
