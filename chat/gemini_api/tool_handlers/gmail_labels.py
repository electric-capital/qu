"""Gmail label handlers: archive_gmail_message plus the Quest-managed label
tools (list_gmail_quest_labels / modify_gmail_labels).
"""

import json
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gmail archive handler
# ---------------------------------------------------------------------------

# Per-user cache of the Gmail label ID for "[Quest]/archived".
# Cleared on server restart. Label IDs are stable once created.
_quest_archived_label_cache: dict[str, str] = {}

_QUEST_PARENT_LABEL_NAME = "[Quest]"
_QUEST_ARCHIVED_LABEL_NAME = "[Quest]/archived"


def _find_or_create_label(service, label_name: str, labels: list[dict]) -> str:
    """Find a label by name in the provided list, or create it.

    Args:
        service: Gmail API service object.
        label_name: The label name to find or create.
        labels: Pre-fetched list of label dicts from labels().list().

    Returns:
        The Gmail label ID string.
    """
    for label in labels:
        if label.get('name') == label_name:
            return label['id']

    # Label not found -- create it
    try:
        created = service.users().labels().create(
            userId='me',
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        ).execute()
        return created['id']
    except Exception as create_exc:
        # Handle race condition: label may have been created concurrently
        error_str = str(create_exc).lower()
        if "already exists" in error_str or "409" in str(create_exc):
            results = service.users().labels().list(userId='me').execute()
            for label in results.get('labels', []):
                if label.get('name') == label_name:
                    return label['id']
        raise


def _ensure_quest_archived_label(service, user_email: str) -> str:
    """Get or create the '[Quest]/archived' Gmail label, returning its ID.

    Ensures the parent '[Quest]' label exists first so that '[Quest]/archived'
    is created as a proper nested label in Gmail's hierarchy.

    Checks the module-level cache first. On cache miss, lists all labels
    to find or create the labels.  The resulting label ID is cached for
    future calls.

    Args:
        service: Gmail API service object (from get_gmail_service).
        user_email: User's email address (cache key).

    Returns:
        The Gmail label ID string for '[Quest]/archived'.

    Raises:
        Exception: If label listing or creation fails.
    """
    # Check cache first
    cached = _quest_archived_label_cache.get(user_email)
    if cached:
        return cached

    # List all labels once and reuse for both lookups
    results = service.users().labels().list(userId='me').execute()
    labels = results.get('labels', [])

    # Ensure parent "quest" label exists first (required for proper nesting)
    _find_or_create_label(service, _QUEST_PARENT_LABEL_NAME, labels)

    # Re-fetch labels after potential parent creation so child creation sees it
    results = service.users().labels().list(userId='me').execute()
    labels = results.get('labels', [])

    # Now ensure "[Quest]/archived" child label exists
    label_id = _find_or_create_label(service, _QUEST_ARCHIVED_LABEL_NAME, labels)
    _quest_archived_label_cache[user_email] = label_id
    return label_id


async def _handle_archive_gmail_message(user: dict, message_id: str, add_labels: list | None = None) -> str:
    """Archive a Gmail message by applying [Quest]/archived label and removing INBOX.

    Loads the user's Google Service credentials, ensures the [Quest]/archived
    label exists (creating it if needed), then modifies the message labels.
    If the modify call fails due to an invalid label ID (label was deleted
    externally), the cache is evicted and the flow is retried once.

    Optionally applies user-configured Quest labels (Settings > Gmail) in the
    same modify call, so archive-and-label doesn't need a separate
    modify_gmail_labels round trip. Label validation is all-or-nothing: an
    unknown name rejects the call without archiving.

    Args:
        user: Authenticated user dict (must have google_services_oauth).
        message_id: Gmail message ID to archive.
        add_labels: Optional configured label names to add alongside
            '[Quest]/archived' (see _handle_modify_gmail_labels).

    Returns:
        JSON string with the result (success confirmation or error details).
    """
    from auth.google_credentials import get_valid_service_credentials
    from api.gmail.helpers import get_gmail_service
    from api.gmail.quest_labels import full_quest_label_name

    if not message_id or not message_id.strip():
        return json.dumps({"error": "message_id is required."})

    # Validate optional extra labels against the user's configured list
    # before touching Gmail, so a bad name can't half-apply.
    add_labels = add_labels or []
    if not isinstance(add_labels, list):
        return json.dumps({"error": "add_labels must be a list of label names."})
    add_names: list[str] = []
    if add_labels:
        configured = await _get_fresh_configured_gmail_labels(user["email"])
        resolved = _resolve_quest_label_names(add_labels, configured, "add_labels")
        if isinstance(resolved, str):
            return json.dumps({"error": resolved})
        add_names = resolved

    # Step 1: Get credentials
    credentials = await get_valid_service_credentials(user)
    if not credentials:
        return json.dumps({
            "error": "google_services_auth_required",
            "message": "Google Services authorization required. Please connect Google Services via Settings > Data Connections.",
        })

    # Step 2: Build Gmail service
    try:
        service = get_gmail_service(credentials)
    except Exception as e:
        return json.dumps({"error": f"Failed to build Gmail service: {e}"})

    user_email = user["email"]

    # Step 3: Get or create label, then modify message (with one retry on label error)
    for attempt in range(2):
        try:
            label_id = _ensure_quest_archived_label(service, user_email)
        except Exception as e:
            error_str = str(e)
            if "403" in error_str or "insufficient" in error_str.lower():
                return json.dumps({
                    "error": "insufficient_scope",
                    "message": (
                        "Gmail modify permission not granted. The user needs to "
                        "disconnect and reconnect Google Services via Settings > "
                        "Data Connections to grant the updated permissions."
                    ),
                })
            return json.dumps({"error": f"Failed to ensure [Quest]/archived label: {e}"})

        # Resolve any extra configured labels to IDs (created on demand).
        # Resolved fresh inside the loop, so a retry after a stale
        # [Quest]/archived cache eviction re-resolves these too.
        extra_label_ids: list[str] = []
        if add_names:
            try:
                # Ensure the parent label exists so children nest properly
                # (a cached [Quest]/archived ID skips the parent check in
                # _ensure_quest_archived_label).
                results = service.users().labels().list(userId='me').execute()
                _find_or_create_label(service, _QUEST_PARENT_LABEL_NAME, results.get('labels', []))
                results = service.users().labels().list(userId='me').execute()
                labels = results.get('labels', [])
                for name in add_names:
                    extra_label_ids.append(
                        _find_or_create_label(service, full_quest_label_name(name), labels)
                    )
            except Exception as e:
                return json.dumps({"error": f"Failed to resolve Quest labels: {e}"})

        try:
            service.users().messages().modify(
                userId='me',
                id=message_id,
                body={
                    "addLabelIds": [label_id] + extra_label_ids,
                    "removeLabelIds": ["INBOX"],
                },
            ).execute()

            success_message = (
                f"Message {message_id} has been archived. "
                f"The '[Quest]/archived' label has been applied and the message "
                f"has been removed from the inbox."
            )
            if add_names:
                applied = ", ".join(f"'{full_quest_label_name(n)}'" for n in add_names)
                success_message += f" Also applied label(s): {applied}."
            result: dict = {
                "status": "success",
                "message_id": message_id,
                "message": success_message,
            }
            if add_names:
                result["added_labels"] = [full_quest_label_name(n) for n in add_names]
            return json.dumps(result)

        except Exception as e:
            error_str = str(e)

            # 403 / insufficient scope
            if "403" in error_str or "insufficient" in error_str.lower():
                return json.dumps({
                    "error": "insufficient_scope",
                    "message": (
                        "Gmail modify permission not granted. The user needs to "
                        "disconnect and reconnect Google Services via Settings > "
                        "Data Connections to grant the updated permissions."
                    ),
                })

            # 404 / message not found
            if "404" in error_str or "not found" in error_str.lower():
                return json.dumps({
                    "error": "message_not_found",
                    "message": f"Gmail message '{message_id}' was not found. It may have been deleted.",
                })

            # Invalid label ID (label was deleted externally) -- evict cache and retry once
            if attempt == 0 and ("400" in error_str or "invalid" in error_str.lower()):
                logger.warning(
                    "[archive_gmail_message] Modify failed (possibly stale label ID), "
                    "evicting cache and retrying. user=%s, message_id=%s, error=%s",
                    user_email, message_id, e,
                )
                _quest_archived_label_cache.pop(user_email, None)
                continue

            # General error
            return json.dumps({"error": f"Failed to archive message: {e}"})

    # Should not reach here, but just in case
    return json.dumps({"error": "Failed to archive message after retry."})


# ---------------------------------------------------------------------------
# Gmail Quest label handlers (user-configured labels via Settings > Gmail)
# ---------------------------------------------------------------------------

# Hard cap on message IDs per modify_gmail_labels call. Gmail's batchModify
# accepts up to 1000 ids; keep the tool bounded well below that.
_MAX_LABEL_MODIFY_MESSAGE_IDS = 100


async def _get_fresh_configured_gmail_labels(user_email: str) -> list[str]:
    """Re-read the user's configured Quest label names from the DB.

    Read fresh (not from the per-turn user snapshot) so Settings edits made
    mid-conversation take effect on the next tool call.
    """
    from db.user_store import get_user_by_email
    from api.gmail.quest_labels import get_configured_gmail_labels

    fresh = await get_user_by_email(user_email)
    return get_configured_gmail_labels((fresh or {}).get("settings"))


async def _handle_list_gmail_quest_labels(user: dict) -> str:
    """List the Gmail labels Quest is allowed to add/remove on messages.

    Reads the user-configured label list (Settings > Gmail) and returns each
    name with its full Gmail label name. No Gmail API call is made -- this
    reflects configuration, not which labels currently exist in Gmail
    (missing ones are created on first use by modify_gmail_labels).
    """
    from api.gmail.quest_labels import full_quest_label_name

    names = await _get_fresh_configured_gmail_labels(user["email"])
    result: dict = {
        "labels": [
            {"name": name, "gmail_label": full_quest_label_name(name)}
            for name in names
        ],
        "archive_label": _QUEST_ARCHIVED_LABEL_NAME,
        "message": (
            "Labels listed here can be added/removed on messages via the "
            "modify_gmail_labels tool (pass the short 'name' values). "
            "Archiving via archive_gmail_message is always available."
        ),
    }
    if not names:
        result["message"] = (
            "No Quest-managed Gmail labels are configured. The user can add "
            "label names under Settings > Gmail. Archiving via "
            "archive_gmail_message is still available."
        )
    return json.dumps(result)


def _resolve_quest_label_names(
    requested: list, configured: list[str], param_name: str,
) -> list[str] | str:
    """Resolve requested label names against the configured list.

    Matching is case-insensitive; the configured casing wins. Returns the
    resolved canonical names, or an error string if any name is unknown or
    the list is malformed.
    """
    by_lower = {name.lower(): name for name in configured}
    resolved: list[str] = []
    for item in requested:
        if not isinstance(item, str) or not item.strip():
            return f"{param_name} must contain non-empty label name strings."
        name = item.strip()
        canonical = by_lower.get(name.lower())
        if canonical is None:
            allowed = ", ".join(f"'{n}'" for n in configured) or "(none configured)"
            return (
                f"Label '{name}' is not in the user's configured Quest label "
                f"list. Allowed labels: {allowed}. The user can change this "
                f"list under Settings > Gmail."
            )
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved


async def _handle_modify_gmail_labels(
    user: dict,
    message_ids: list,
    add_labels: list,
    remove_labels: list,
) -> str:
    """Add/remove user-configured Quest labels on one or more Gmail messages.

    Only label names present in the user's configured list (Settings > Gmail)
    may be used; each maps to the nested Gmail label '[Quest]/<name>'. Labels
    being added are created in Gmail on demand (under the '[Quest]' parent);
    labels being removed that don't exist in Gmail are skipped as no-ops.
    All message IDs are modified in a single Gmail batchModify call.

    Args:
        user: Authenticated user dict (must have google_services_oauth).
        message_ids: Gmail message IDs to modify (1..100).
        add_labels: Configured label names to add (may be empty).
        remove_labels: Configured label names to remove (may be empty).

    Returns:
        JSON string with the result (success summary or error details).
    """
    from auth.google_credentials import get_valid_service_credentials
    from api.gmail.helpers import get_gmail_service
    from api.gmail.quest_labels import full_quest_label_name

    # -- Validate message ids -------------------------------------------------
    if not isinstance(message_ids, list) or not message_ids:
        return json.dumps({"error": "message_ids must be a non-empty list of Gmail message IDs."})
    if len(message_ids) > _MAX_LABEL_MODIFY_MESSAGE_IDS:
        return json.dumps({
            "error": (
                f"Too many message IDs ({len(message_ids)}). At most "
                f"{_MAX_LABEL_MODIFY_MESSAGE_IDS} per call; split into multiple calls."
            ),
        })
    ids: list[str] = []
    for item in message_ids:
        if not isinstance(item, str) or not item.strip():
            return json.dumps({"error": "message_ids must contain non-empty string IDs."})
        if item.strip() not in ids:
            ids.append(item.strip())

    # -- Validate label names against the configured list ---------------------
    add_labels = add_labels or []
    remove_labels = remove_labels or []
    if not isinstance(add_labels, list) or not isinstance(remove_labels, list):
        return json.dumps({"error": "add_labels and remove_labels must be lists of label names."})
    if not add_labels and not remove_labels:
        return json.dumps({"error": "Provide at least one label in add_labels or remove_labels."})

    configured = await _get_fresh_configured_gmail_labels(user["email"])
    add_names = _resolve_quest_label_names(add_labels, configured, "add_labels")
    if isinstance(add_names, str):
        return json.dumps({"error": add_names})
    remove_names = _resolve_quest_label_names(remove_labels, configured, "remove_labels")
    if isinstance(remove_names, str):
        return json.dumps({"error": remove_names})
    overlap = set(n.lower() for n in add_names) & set(n.lower() for n in remove_names)
    if overlap:
        return json.dumps({"error": f"Labels cannot be both added and removed: {sorted(overlap)}."})

    # -- Credentials + service ------------------------------------------------
    credentials = await get_valid_service_credentials(user)
    if not credentials:
        return json.dumps({
            "error": "google_services_auth_required",
            "message": "Google Services authorization required. Please connect Google Services via Settings > Data Connections.",
        })
    try:
        service = get_gmail_service(credentials)
    except Exception as e:
        return json.dumps({"error": f"Failed to build Gmail service: {e}"})

    # -- Resolve label IDs (create add-labels on demand) ----------------------
    # Labels are listed fresh on every call (no per-user cache like the
    # archive handler): one extra round trip buys immunity to stale IDs
    # across an arbitrary user-configured label set.
    try:
        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])

        add_label_ids: list[str] = []
        if add_names:
            # Ensure the parent label exists first so children nest properly.
            _find_or_create_label(service, _QUEST_PARENT_LABEL_NAME, labels)
            results = service.users().labels().list(userId='me').execute()
            labels = results.get('labels', [])
            for name in add_names:
                add_label_ids.append(
                    _find_or_create_label(service, full_quest_label_name(name), labels)
                )

        remove_label_ids: list[str] = []
        skipped_remove: list[str] = []
        existing_by_name = {label.get('name'): label['id'] for label in labels}
        for name in remove_names:
            label_id = existing_by_name.get(full_quest_label_name(name))
            if label_id is None:
                # Label doesn't exist in Gmail -- nothing to remove.
                skipped_remove.append(name)
            else:
                remove_label_ids.append(label_id)
    except Exception as e:
        error_str = str(e)
        if "403" in error_str or "insufficient" in error_str.lower():
            return json.dumps({
                "error": "insufficient_scope",
                "message": (
                    "Gmail modify permission not granted. The user needs to "
                    "disconnect and reconnect Google Services via Settings > "
                    "Data Connections to grant the updated permissions."
                ),
            })
        return json.dumps({"error": f"Failed to resolve Gmail labels: {e}"})

    if not add_label_ids and not remove_label_ids:
        return json.dumps({
            "status": "success",
            "modified_count": 0,
            "skipped_remove_labels": skipped_remove,
            "message": "None of the requested remove-labels exist in Gmail; nothing to do.",
        })

    # -- Apply to all messages in one batch call ------------------------------
    body: dict = {"ids": ids}
    if add_label_ids:
        body["addLabelIds"] = add_label_ids
    if remove_label_ids:
        body["removeLabelIds"] = remove_label_ids
    try:
        service.users().messages().batchModify(userId='me', body=body).execute()
    except Exception as e:
        error_str = str(e)
        if "403" in error_str or "insufficient" in error_str.lower():
            return json.dumps({
                "error": "insufficient_scope",
                "message": (
                    "Gmail modify permission not granted. The user needs to "
                    "disconnect and reconnect Google Services via Settings > "
                    "Data Connections to grant the updated permissions."
                ),
            })
        if "404" in error_str or "not found" in error_str.lower():
            return json.dumps({
                "error": "message_not_found",
                "message": (
                    "One or more message IDs were not found; no labels were "
                    "changed. Verify the IDs and retry."
                ),
            })
        return json.dumps({"error": f"Failed to modify message labels: {e}"})

    result: dict = {
        "status": "success",
        "modified_count": len(ids),
        "added_labels": [full_quest_label_name(n) for n in add_names],
        "removed_labels": [full_quest_label_name(n) for n in remove_names if n not in skipped_remove],
        "message": f"Labels updated on {len(ids)} message(s).",
    }
    if skipped_remove:
        result["skipped_remove_labels"] = skipped_remove
        result["message"] += (
            " Some remove-labels did not exist in Gmail and were skipped: "
            + ", ".join(skipped_remove) + "."
        )
    return json.dumps(result)

