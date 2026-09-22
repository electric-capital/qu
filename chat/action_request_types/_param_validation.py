"""Shared helper for rejecting unknown keys in action-request param dicts.

Every ``handler.validate_params(...)`` calls
:func:`reject_unknown_params` as its first step, so an unknown top-level
key fails fast with ``ValueError`` before any required-field /
type-coercion check runs. The dispatch arm at
``chat/gemini_api/conversation.py:1202-1211`` already maps that
``ValueError`` to a synchronous ``{"error": "Invalid parameters: ..."}``
tool result -- no DB row, no wait handle, no card, no suspend -- which
gives the model a same-turn signal to self-correct.

The same allow-list pattern is reused inside per-row sub-validators
(nested attachment rows and other list-of-dict params) via the
``path`` argument so the resulting message keeps the existing
path-style format (e.g. ``attachments[0].file_name``).
"""

from __future__ import annotations

from typing import Iterable


def reject_unknown_params(
    request_type: str,
    params: dict,
    allowed: Iterable[str],
    *,
    path: str | None = None,
) -> None:
    """Raise ``ValueError`` if ``params`` carries any key outside ``allowed``.

    Args:
        request_type: The action ``request_type`` string (or the
            sub-validator's logical name) used in the error message.
        params: The raw param dict (top-level or a nested junction row).
        allowed: The set of accepted keys at this level.
        path: Optional path prefix (e.g. ``"names[0]"``) so nested-row
            errors include the path -- matching the existing
            ``names[0].name_type is required`` style produced by the
            same validators.

    The error message lists the unknown keys (sorted, for deterministic
    test output) and the full allowed set (also sorted) so the model
    can self-correct.
    """
    allowed_set = frozenset(allowed)
    extra = sorted(set(params) - allowed_set)
    if not extra:
        return

    if path:
        # Nested-row error: list keys with the path prefix so the model
        # sees `attachments[0].file_name` rather than just `file_name`.
        keyed = [f"{path}.{k}" for k in extra]
        raise ValueError(
            f"Unknown parameter for {request_type}: {keyed!r}. "
            f"Allowed parameters: {sorted(allowed_set)}."
        )

    raise ValueError(
        f"Unknown parameter for {request_type}: {extra!r}. "
        f"Allowed parameters: {sorted(allowed_set)}."
    )
