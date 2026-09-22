"""Slack pending-request notifier daemon.

Runs as an asyncio background task within the FastAPI process (started
and cancelled from the FastAPI lifespan in quest.py, mirroring
chat/scheduler.py). Periodically finds users with open ("unanswered")
action requests and sends them a Slack bot DM reminder.

A user is reminded only when their oldest open request has itself been
open for at least the reminder interval (so a card the user is about to
click is never insta-DMed), and then at most once per interval while any
open request remains. The interval defaults to DEFAULT_INTERVAL_MINUTES
and is tunable per user via the ``slack_pending_notification_interval_minutes``
key in ``users.settings`` (Settings > Slack); the feature can be turned
off per user with ``slack_pending_notifications_enabled`` (absent = on).

DMs go out over the shared bot token (chat.postMessage to the user's
Slack user id from their per-user Slack OAuth record) -- Socket Mode is
not required. When the bot token or the user's Slack connection is
missing the user is skipped silently, so the loop is inert on
deployments without Slack.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# How often the notifier scans for users with open requests.
POLL_INTERVAL_SECONDS = 60

# Default reminder cadence, overridable per user via
# users.settings.slack_pending_notification_interval_minutes.
DEFAULT_INTERVAL_MINUTES = 60

# Bounds enforced on the per-user interval setting (also validated at
# write time in chat/routes/user.py -- keep in step).
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440

# Per-process throttle state: user_id -> last DM sent at (aware UTC).
# Lost on restart, which at worst re-sends one reminder early -- the
# oldest-request age gate still applies.
_last_notified: dict[int, datetime] = {}


async def notifier_loop() -> None:
    """Main loop. Runs forever, scanning for users to remind."""
    logger.info(
        "[slack-notifier] Pending-request notifier started "
        "(poll interval: %ds, default reminder interval: %dm)",
        POLL_INTERVAL_SECONDS, DEFAULT_INTERVAL_MINUTES,
    )

    while True:
        try:
            await _poll_once()
        except asyncio.CancelledError:
            logger.info("[slack-notifier] Notifier loop cancelled, shutting down")
            raise
        except Exception:
            logger.exception("[slack-notifier] Error in notifier poll cycle")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def resolve_interval_minutes(settings: dict) -> int:
    """The user's reminder interval in minutes, clamped to sane bounds."""
    raw = settings.get("slack_pending_notification_interval_minutes")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return DEFAULT_INTERVAL_MINUTES
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, raw))


def _slack_user_id_for(user: dict) -> Optional[str]:
    """The user's Slack user id from their per-user OAuth record, or None.

    Mirrors the oauth_blob shape read by get_user_by_slack_user_id() in
    db/user_store.py (written by the Slack plugin's /auth/slack callback).
    """
    row = (user.get("service_credentials") or {}).get("slack") or {}
    blob = row.get("oauth_blob")
    if not isinstance(blob, dict) or not blob.get("access_token"):
        return None
    slack_user_id = blob.get("user_id")
    return slack_user_id if isinstance(slack_user_id, str) and slack_user_id else None


def _as_aware_utc(dt: datetime) -> datetime:
    """SQLite DateTime columns come back naive; treat them as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _build_reminder(open_count: int) -> tuple[str, list[dict]]:
    """The (fallback_text, blocks) for a pending-request reminder DM."""
    from config.server_config import get_app_base_url

    noun = "request" if open_count == 1 else "requests"
    base_url = get_app_base_url()
    text = (
        f":hourglass_flowing_sand: You have {open_count} unanswered {noun} in "
        "Quest waiting for your review."
    )
    if base_url:
        # /inbox deep-links straight into the requests inbox (RequestsView);
        # served by the SPA route in quest.py.
        text += f" <{base_url}/inbox|Open your inbox>"
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "_You can adjust or turn off these reminders in "
                        "Settings > Slack._"
                    ),
                }
            ],
        },
    ]
    # The plain-text fallback (notification banners etc.) must not carry
    # mrkdwn link syntax.
    fallback = (
        f"You have {open_count} unanswered {noun} in Quest "
        "waiting for your review."
    )
    return fallback, blocks


async def _poll_once() -> None:
    """Single poll cycle: remind every user whose reminder is due."""
    from db.action_request_store import list_open_request_summaries

    summaries = await list_open_request_summaries()

    # Forget users who no longer have open requests so their next pending
    # request starts a fresh interval (and the dict stays bounded).
    active_ids = {s["user_id"] for s in summaries}
    for user_id in list(_last_notified):
        if user_id not in active_ids:
            del _last_notified[user_id]

    if not summaries:
        return

    # Loaded fresh each cycle so admin credential changes apply without a
    # restart. Missing token = Slack not configured; nothing to do.
    from auth.config import load_slack_bot_token
    try:
        bot_token = load_slack_bot_token()
    except Exception:
        logger.debug("[slack-notifier] Slack bot token not configured; skipping cycle")
        return

    from db.user_store import get_user_by_id

    now = datetime.now(timezone.utc)
    for summary in summaries:
        user_id = summary["user_id"]
        try:
            user = await get_user_by_id(user_id)
            if not user:
                continue
            settings = user.get("settings") or {}
            if settings.get("slack_pending_notifications_enabled") is False:
                continue
            slack_user_id = _slack_user_id_for(user)
            if not slack_user_id:
                continue

            interval = timedelta(minutes=resolve_interval_minutes(settings))
            oldest = _as_aware_utc(summary["oldest_created_at"])
            if now - oldest < interval:
                continue
            last = _last_notified.get(user_id)
            if last is not None and now - last < interval:
                continue

            # Recorded before the send so a persistent Slack error waits
            # out the same interval instead of retrying every poll tick.
            _last_notified[user_id] = now
            await _send_reminder(bot_token, slack_user_id, summary["open_count"])
            logger.info(
                "[slack-notifier] Sent pending-request reminder to user %s "
                "(%d open, oldest %s)",
                user_id, summary["open_count"], oldest.isoformat(),
            )
        except Exception:
            logger.warning(
                "[slack-notifier] Failed to send reminder to user %s",
                user_id, exc_info=True,
            )


async def _send_reminder(bot_token: str, slack_user_id: str, open_count: int) -> None:
    """Post the reminder DM. Slack accepts a bare user id as channel."""
    from slack_sdk.web.async_client import AsyncWebClient

    fallback, blocks = _build_reminder(open_count)
    client = AsyncWebClient(token=bot_token)
    await client.chat_postMessage(
        channel=slack_user_id, text=fallback, blocks=blocks,
    )
