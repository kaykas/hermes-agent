"""skill_view repeat-view dedup registry: per-task cache of (resolved skill name, file_path) ->
digest of the skill_view result served. A repeat view whose result is byte-identical returns a short
stub — the earlier tool result already carries the content verbatim. Content digests need no file
timestamps, so a same-size rewrite in one coarse mtime tick (or on a host whose clock disagrees with
the filesystem's) is never mistaken for "unchanged". Cleared via ``reset_skill_view_dedup()`` on
context compression AND on a committed proactive tool-result prune, because both replace the
original content with a one-line marker. Each entry also keeps the tool call that served it, so a
view the model received as anything but that whole result (a spill preview, a guardrail reference
stub) is released by that call (``release_replaced_skill_view``); a notice appended after the whole
result keeps it.
"""

import difflib
import hashlib
import json
import threading

_skill_view_tracker: dict[str, dict[tuple, tuple[str, str, int]]] = {}
_skill_view_tracker_lock = threading.Lock()
# Per-turn missing-slug inventory and responses. The first miss snapshots the
# skills_list names once; later misses reuse that inventory, and an exact repeat
# is refused before any filesystem/plugin lookup runs.
_missing_skill_tracker: dict[str, dict] = {}
_SKILL_VIEW_DEDUP_CAP = 200

_SKILL_VIEW_DEDUP_MESSAGE = (
    "Skill content unchanged since it was loaded earlier in this "
    "conversation — refer to the earlier skill_view result; it is still "
    "current and complete. (Re-issued after context compression, this "
    "returns the full content again.)")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dedup_skill_view(task_id, name, file_path, payload: dict, result: str) -> str | None:
    """The "unchanged" stub when this task was already served exactly ``result`` for this skill
    file; otherwise record ``result``'s digest and return None (serve the full result)."""
    # Never dedup setup-needed views: readiness depends on config/env state that
    # changes without the file changing; the model must see the refreshed status.
    if (not task_id or payload.get("setup_needed")
            or payload.get("readiness_status") == "setup_needed"):
        return None
    from tools.approval_context import _approval_tool_call_id

    resolved = str(payload.get("name") or name)
    key = (resolved, file_path or "")
    digest = _digest(result)
    with _skill_view_tracker_lock:
        cache = _skill_view_tracker.setdefault(str(task_id), {})
        if (entry := cache.get(key)) and entry[0] == digest:
            return json.dumps({
                "success": True, "status": "unchanged", "name": resolved,
                "file": file_path or "SKILL.md", "dedup": True, "content_returned": False,
                "message": _SKILL_VIEW_DEDUP_MESSAGE}, ensure_ascii=False)
        cache.pop(key, None)  # re-insert at the end: FIFO eviction keeps the recently served
        cache[key] = (digest, _approval_tool_call_id.get(), len(result))
        while len(cache) > _SKILL_VIEW_DEDUP_CAP:  # FIFO eviction
            del cache[next(iter(cache))]
    return None


def release_replaced_skill_view(task_id, tool_call_id, delivered) -> None:
    """Forget the view ``tool_call_id`` recorded unless ``delivered`` starts with that whole result: a
    spill preview or a reference stub in its place leaves the model without the content a later stub
    would point at, while a guardrail notice appended after it does not."""
    if not tool_call_id:
        return
    with _skill_view_tracker_lock:
        cache = _skill_view_tracker.get(str(task_id)) or {}
        key = next((key for key, (_, call_id, _) in cache.items() if call_id == tool_call_id), None)
        if key is None:
            return
        digest, _, length = cache[key]
        if not isinstance(delivered, str) or _digest(delivered[:length]) != digest:
            del cache[key]


def release_replaced_skill_views(task_id, batch: list[dict], contents_before: list) -> None:
    """``release_replaced_skill_view`` for each row of a batch whose content the turn budget replaced."""
    for message, before in zip(batch, contents_before):
        if message.get("content") is not before:
            release_replaced_skill_view(task_id, message.get("tool_call_id"), message.get("content"))


def repeated_missing_skill(task_id, name):
    """Return the cached closest matches for an exact repeat miss, else None."""
    if not task_id:
        return None
    with _skill_view_tracker_lock:
        entry = _missing_skill_tracker.get(str(task_id), {}).get("misses", {}).get(name)
        return list(entry) if entry is not None else None


def record_missing_skill(task_id, name, inventory_provider):
    """Cache one skills_list inventory per turn and return closest installed slugs.

    ``inventory_provider`` runs only when this turn has no inventory yet. Exact
    repeats are served by ``repeated_missing_skill`` before this function is reached.
    """
    inventory = None
    if task_id:
        with _skill_view_tracker_lock:
            inventory = _missing_skill_tracker.get(str(task_id), {}).get("inventory")
    if inventory is None:
        inventory = sorted({str(item) for item in inventory_provider()})
    suggestions = difflib.get_close_matches(name, inventory, n=5, cutoff=0.2)
    if not suggestions:
        lowered = name.lower()
        suggestions = [item for item in inventory if lowered in item.lower()][:5]
    if not suggestions:
        suggestions = inventory[:5]
    if task_id:
        with _skill_view_tracker_lock:
            entry = _missing_skill_tracker.setdefault(str(task_id), {})
            entry.setdefault("inventory", inventory)
            entry.setdefault("misses", {})[name] = list(suggestions)
    return suggestions


def reset_skill_view_dedup(task_id: str | None = None) -> None:
    """Clear view and missing-slug caches (all tasks when task_id is None)."""
    with _skill_view_tracker_lock:
        if task_id is None:
            _skill_view_tracker.clear()
            _missing_skill_tracker.clear()
        else:
            _skill_view_tracker.pop(str(task_id), None)
            _missing_skill_tracker.pop(str(task_id), None)
