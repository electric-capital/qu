"""Per-routine inference cost report (Routine Settings > Costs).

Assembles the figures the Costs section of ``RoutineSettingsModal`` shows
for one routine: rolling 7- and 28-day totals with the preceding period for
the delta, the routine's lifetime total, and a per-run table of the most
recent runs.

Attribution model -- one run = one conversation the routine created
(``conversations.routine_id``); a run's cost is the conversation's whole
recorded usage (top-level + sub-agent calls, every model), and every run is
bucketed by the moment it STARTED (``conversations.created_at``), not by
each call's timestamp. Routine runs are short, so the two agree in
practice, and keying everything on the run start keeps the window totals,
run counts and the per-run table mutually consistent (the 7-day total is
exactly the sum of the runs listed as started in the last 7 days). This
differs from the admin Users report, which slices routine spend by call
time; the two are different views, not the same figure.

Cost figures follow the repo-wide null-on-unpriced convention: a run whose
usage includes a model with no pricing entry (and no provider-reported
amount) has a null ``cost_usd``, and any window containing such a run is
null too -- a partial sum would read as the whole figure. Each figure
carries a ``cost_source`` (reported / estimated / mixed, see
db/llm_call_store.py) so the UI can mark estimates with "~".

Not covered: runs whose conversation was deleted (the call rows survive but
lose their routine link) and anything that happened before raw-usage
capture existed.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from chat.storage import ChatStorage
from db.conversation_store import (
    list_routine_conversation_rows,
    routine_conversation_ids_query,
)
from db.llm_call_store import add_cost, get_usage_by_model_for_conversation_query

# Rolling windows shown as headline cards, in display order.
COST_WINDOW_DAYS = (7, 28)

# Rows in the recent-runs table.
RECENT_RUNS_LIMIT = 10


def _new_bucket() -> dict:
    return {
        "run_count": 0,
        "call_count": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cost_source": None,
    }


def _fold_run(bucket: dict, run: dict) -> None:
    bucket["run_count"] += 1
    bucket["call_count"] += run["call_count"]
    bucket["total_tokens"] += run["total_tokens"]
    add_cost(bucket, "cost_usd", "cost_source", run["cost_usd"], run["cost_source"])


def _finish_bucket(bucket: dict) -> dict:
    if bucket["cost_usd"] is not None:
        bucket["cost_usd"] = round(bucket["cost_usd"], 6)
    return bucket


def _as_utc(value: datetime) -> datetime:
    """Conversation timestamps are stored naive-UTC; compare them as aware."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_view(row: dict, usage: Optional[dict]) -> dict:
    """One row of the recent-runs table: identity + the run's usage total."""
    total = usage["total"] if usage else None
    models = [m["model"] for m in usage["models"]] if usage else []
    return {
        "conversation_id": row["id"],
        "title": ChatStorage._resolve_list_title(row["id"], row),
        "started_at": _as_utc(row["created_at"]).isoformat(),
        # Every model that made a call during the run (sub-agents included),
        # heaviest first; the conversation's own model column as a fallback
        # for a run that recorded no calls (e.g. failed before the first).
        "models": models or ([row["model"]] if row["model"] else []),
        "call_count": total["call_count"] if total else 0,
        "total_tokens": total["total_tokens"] if total else 0,
        # A run with no recorded calls cost nothing, and that is a known
        # figure -- distinct from an unpriced (null) run.
        "cost_usd": total["estimated_cost_usd"] if total else 0.0,
        "cost_source": total["cost_source"] if total else None,
    }


def build_cost_report(
    routine: dict,
    conversation_rows: list[dict],
    usage_by_conversation: dict[str, dict],
    now: Optional[datetime] = None,
) -> dict:
    """Fold per-run usage into the Costs section response (pure function).

    Args:
        routine: The routine row dict (``id``, ``created_at``).
        conversation_rows: ``list_routine_conversation_rows()`` output,
            newest first.
        usage_by_conversation: ``get_usage_by_model_for_conversation_query``
            output keyed by conversation id; runs without calls are absent.
        now: Reference instant for the rolling windows (tests inject it).

    Returns:
        {
            "routine_id": str,
            "routine_created_at": ISO str | None,
            "generated_at": ISO str,
            "windows": [   # one per COST_WINDOW_DAYS, in order
                {"days": 7,
                 "current": {"run_count", "call_count", "total_tokens",
                             "cost_usd", "cost_source"},   # [now-7d, now)
                 "previous": {...same keys...}},           # [now-14d, now-7d)
                ...
            ],
            "lifetime": {...same bucket keys...},   # every surviving run
            "recent_runs": [ _run_view rows, newest first, <= RECENT_RUNS_LIMIT ],
        }
    """
    now = _as_utc(now) if now is not None else datetime.now(timezone.utc)

    runs = [_run_view(row, usage_by_conversation.get(row["id"])) for row in conversation_rows]
    starts = [_as_utc(row["created_at"]) for row in conversation_rows]

    lifetime = _new_bucket()
    windows = [
        {"days": days, "current": _new_bucket(), "previous": _new_bucket()}
        for days in COST_WINDOW_DAYS
    ]
    for run, started in zip(runs, starts):
        _fold_run(lifetime, run)
        age = now - started
        for window in windows:
            span = timedelta(days=window["days"])
            if age < span:
                _fold_run(window["current"], run)
            elif age < 2 * span:
                _fold_run(window["previous"], run)

    for window in windows:
        _finish_bucket(window["current"])
        _finish_bucket(window["previous"])

    routine_created = routine.get("created_at")
    if isinstance(routine_created, datetime):
        routine_created = _as_utc(routine_created).isoformat()

    return {
        "routine_id": routine["id"],
        "routine_created_at": routine_created,
        "generated_at": now.isoformat(),
        "windows": windows,
        "lifetime": _finish_bucket(lifetime),
        "recent_runs": runs[:RECENT_RUNS_LIMIT],
    }


async def get_routine_cost_report(routine: dict) -> dict:
    """Load a routine's runs and their usage and build the Costs report."""
    rows = await list_routine_conversation_rows(routine["id"])
    usage = await get_usage_by_model_for_conversation_query(
        routine_conversation_ids_query(routine["id"])
    )
    return build_cost_report(routine, rows, usage)
