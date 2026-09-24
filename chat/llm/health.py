"""Live health checks for inference models, plus the model-health store.

Backs the admin Settings > Inference Providers per-model checks: a tiny real
inference call through the same provider instances (and therefore the same
cached SDK clients, credentials, and project/region resolution) that real
conversations use. A live call is the only check that catches every failure
mode an enabled-in-Quest-but-broken model can have -- most importantly Claude
models on Vertex that were never enabled in Model Garden, but also revoked
API keys, wrong project/region, and exhausted quota.

Results are server-global state, not per-request answers: ``ModelHealthStore``
keeps the latest per-model verdict in memory and mirrors it to
``data/model_health.json`` so it survives restarts. All configured models are
checked once at startup (``run_startup_model_checks`` from the quest
lifespan), and the admin panel reads/re-triggers checks against the same
store. The store drives model-picker filtering: ``get_available_models()``
drops models with a failing verdict, so the frontend picker only offers
models that actually answered their last check (never-checked models stay
visible). Sweeps therefore always iterate ``get_configured_models()`` -- the
health-blind config-presence list -- so a failing model keeps being rechecked
and can recover.

Failures are returned as data (``{"ok": False, "error": ...}``) rather than
raised: the consumer is admin diagnostics whose whole point is to display
the extracted error.
"""

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Generous ceiling: a healthy check answers in a couple of seconds, but a
# stalled Vertex stream would otherwise hang the admin request forever.
CHECK_TIMEOUT_SECONDS = 45

# Keep displayed errors bounded; provider bodies can embed whole HTML pages.
_MAX_ERROR_LENGTH = 600


def extract_error_message(exc: BaseException) -> str:
    """Extract a compact human-readable message from a provider SDK error.

    Handles both SDK families without importing either:

    - anthropic ``APIStatusError``: ``status_code`` plus a ``body`` dict whose
      ``error.message`` carries the actual Vertex explanation.
    - google-genai ``APIError``: ``code`` plus a ``message`` attribute.

    Anything else falls back to ``str(exc)``.
    """
    message = None
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            message = str(error["message"])
    if message is None:
        raw = getattr(exc, "message", None)
        if isinstance(raw, str) and raw.strip():
            message = raw.strip()
    if message is None:
        message = str(exc).strip() or exc.__class__.__name__

    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int):
        prefix = f"HTTP {status}: "
        if not message.startswith(prefix):
            message = prefix + message
    if len(message) > _MAX_ERROR_LENGTH:
        message = message[: _MAX_ERROR_LENGTH - 3] + "..."
    return message


async def check_model(model_id: str) -> dict:
    """Run a minimal live inference call for ``model_id``.

    Returns ``{"model", "ok", "error"}`` where ``error`` is a compact
    extracted message (None on success). Never raises for provider or
    configuration failures -- those are the interesting results.
    """
    from chat.llm.config import get_provider_instance, resolve_model

    spec = resolve_model(model_id)
    if spec is None:
        return {
            "model": model_id,
            "ok": False,
            "error": f"Unknown model: {model_id}",
        }
    try:
        provider = get_provider_instance(spec.provider, spec.instance_id)
        await asyncio.wait_for(
            provider.check_model_access(model_id), timeout=CHECK_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        return {
            "model": model_id,
            "ok": False,
            "error": f"Timed out after {CHECK_TIMEOUT_SECONDS}s waiting for a response.",
        }
    except Exception as exc:
        error = extract_error_message(exc)
        logger.info("Model health check failed for %s: %s", model_id, error)
        return {"model": model_id, "ok": False, "error": error}
    return {"model": model_id, "ok": True, "error": None}


# ---------------------------------------------------------------------------
# Model-health store
# ---------------------------------------------------------------------------

# Startup fans out one check per configured model; bound the burst so a
# dozen simultaneous first-requests through one Vertex project can't trip
# spurious per-project rate limits and pollute the results they exist to
# produce.
_STARTUP_CHECK_CONCURRENCY = 4


class ModelHealthStore:
    """Latest per-model health verdicts, in memory and mirrored to JSON.

    Entries are ``{"ok": bool, "error": str | None, "checked_at": iso8601}``
    keyed by registry model id. Reads are synchronous against the in-memory
    map (lazily loaded from disk); writes go through :meth:`record`, which
    persists atomically. Concurrent checks are fine -- per-model last write
    wins, and the file write is serialized by a lock.
    """

    def __init__(self, path: Path):
        self._path = path
        self._statuses: dict[str, dict] = {}
        self._loaded = False
        self._write_lock = asyncio.Lock()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            logger.warning("Unreadable model health file: %s", self._path)
            return
        models = data.get("models") if isinstance(data, dict) else None
        if isinstance(models, dict):
            self._statuses = {
                model_id: status
                for model_id, status in models.items()
                if isinstance(status, dict)
            }

    def get_all(self) -> dict[str, dict]:
        """Return the current per-model status map (shared, do not mutate)."""
        self._ensure_loaded()
        return self._statuses

    def get(self, model_id: str) -> dict | None:
        self._ensure_loaded()
        return self._statuses.get(model_id)

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".model_health.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"models": self._statuses}, f, indent=2)
            os.replace(tmp_path, self._path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def record(self, model_id: str, ok: bool, error: str | None) -> dict:
        """Store one verdict and persist; returns the stored entry."""
        self._ensure_loaded()
        status = {
            "ok": ok,
            "error": error,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._statuses[model_id] = status
        async with self._write_lock:
            try:
                self._persist()
            except OSError:
                logger.warning(
                    "Failed to persist model health to %s", self._path, exc_info=True
                )
        return status

    async def forget(self, model_ids: list[str]) -> None:
        """Drop the stored verdicts for models that no longer exist
        (removed from an instance, or the instance was deleted)."""
        self._ensure_loaded()
        dropped = False
        for model_id in model_ids:
            if self._statuses.pop(model_id, None) is not None:
                dropped = True
        if not dropped:
            return
        async with self._write_lock:
            try:
                self._persist()
            except OSError:
                logger.warning(
                    "Failed to persist model health to %s", self._path, exc_info=True
                )

    async def run_check(self, model_id: str) -> dict:
        """Run a live check and record the verdict.

        Returns ``{"model", "ok", "error", "checked_at"}``.
        """
        result = await check_model(model_id)
        status = await self.record(model_id, result["ok"], result["error"])
        return {"model": model_id, **status}

    async def run_startup_checks(self) -> None:
        """Check every model whose backend credentials appear configured.

        Iterates ``get_configured_models()`` -- the config-presence list, so
        an unconfigured deployment runs zero checks. Deliberately NOT the
        health-filtered ``get_available_models()``: a model hidden by a
        failing verdict must still be swept so it can recover. Results
        replace any stale verdicts loaded from disk.
        """
        from chat.llm.config import get_configured_models

        models = get_configured_models()
        if not models:
            logger.info("Model health: no configured models to check")
            return
        semaphore = asyncio.Semaphore(_STARTUP_CHECK_CONCURRENCY)

        async def _bounded_check(model_id: str) -> dict:
            async with semaphore:
                return await self.run_check(model_id)

        results = await asyncio.gather(*(_bounded_check(m) for m in models))
        failed = [r for r in results if not r["ok"]]
        logger.info(
            "Model health: checked %d model(s), %d failing%s",
            len(results),
            len(failed),
            (
                " (" + ", ".join(r["model"] for r in failed) + ")"
                if failed
                else ""
            ),
        )


_store: ModelHealthStore | None = None


def get_model_health_store() -> ModelHealthStore:
    """Process-wide store singleton (tests swap ``_store`` for isolation)."""
    global _store
    if _store is None:
        from config.paths import MODEL_HEALTH_FILE

        _store = ModelHealthStore(MODEL_HEALTH_FILE)
    return _store


# Strong references to in-flight background recheck tasks; without them a
# fire-and-forget task can be garbage-collected mid-run.
_recheck_tasks: set[asyncio.Task] = set()


def schedule_model_rechecks(model_ids: list[str]) -> "asyncio.Task | None":
    """Recheck ``model_ids`` in the background (sequentially, best-effort).

    Used after an inference credential change: models hidden from the picker
    by a failing verdict recorded under the old credentials come back as soon
    as a check under the new credentials succeeds, without waiting for a
    restart or a manual per-model recheck. Sequential on purpose -- the lists
    are tiny and a burst through one project can trip rate limits.

    Returns the task (mainly for tests); None when there is nothing to check.
    """
    if not model_ids:
        return None

    async def _recheck() -> None:
        store = get_model_health_store()
        for model_id in model_ids:
            try:
                await store.run_check(model_id)
            except Exception:
                logger.exception("Background recheck failed for %s", model_id)

    task = asyncio.create_task(_recheck())
    _recheck_tasks.add(task)
    task.add_done_callback(_recheck_tasks.discard)
    return task


async def run_startup_model_checks() -> None:
    """Lifespan entry point: check all configured models in the background.

    Never lets a health-check problem interfere with server boot.
    """
    try:
        await get_model_health_store().run_startup_checks()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Startup model health checks failed; continuing")
