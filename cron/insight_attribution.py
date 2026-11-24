"""Post-delivery attribution for opt-in cron insight jobs.

The agent's pre-delivery ``sent-ids.json`` write is an intent record, not proof
of operator delivery.  This module joins that intent to a confirmed platform
message id only after the scheduler's send lane succeeds.  It never sends and
never rewrites ``sent-ids.json``.

Configuration is deliberately per-job and disabled by default::

    "insight_attribution": {
        "enabled": true,
        "sent_ids_path": "/optional/override/sent-ids.json",
        "drafts_dir": "/optional/override/insight-drafts",
        "receipts_path": "/optional/override/delivery-receipts.jsonl"
    }

With no path overrides, all files live under the active profile's
``insight-drafts`` directory.  Only rich ``entries`` rows (which carry
``sent_at``) are attribution candidates; legacy date arrays and ``sent_ids``
are read neither as proof nor rewritten/normalized.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger("cron.scheduler")
SCHEMA = "cron-insight-delivery-v1"


def _parse_time(value: Any) -> Optional[dt.datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _atomic_write_json(path: Path, value: Any) -> None:
    """Replace one JSON document without exposing a partial write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _paths(job: dict) -> tuple[Path, Path, Path]:
    config = job.get("insight_attribution") or {}
    root = get_hermes_home().resolve() / "insight-drafts"
    sent_ids = Path(config.get("sent_ids_path") or root / "sent-ids.json")
    drafts_dir = Path(config.get("drafts_dir") or root)
    receipts = Path(config.get("receipts_path") or root / "delivery-receipts.jsonl")
    return sent_ids, drafts_dir, receipts


def _execution_started_at(job: dict) -> Optional[dt.datetime]:
    execution_id = str(job.get("execution_id") or "")
    if not execution_id:
        return None
    # Import late so queued-delivery drains and tests use the active profile home.
    from cron.executions import get_execution

    execution = get_execution(execution_id) or {}
    return _parse_time(execution.get("started_at") or execution.get("claimed_at"))


def _candidate_ids(sent_ids_path: Path, started_at: dt.datetime, delivered_at: dt.datetime) -> list[str]:
    """Return only rich intent rows written during this execution.

    The legacy top-level arrays are intentionally not flattened into entries:
    they have no execution timestamp, so attributing them would revive old
    intents on the first enabled run.  Companion ids are part of the same rich
    intent row and therefore share its delivery receipt.
    """
    data = _read_json(sent_ids_path)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sent_at = _parse_time(entry.get("sent_at"))
        # Intent timestamps are minute-precision in existing Nova2 entries. Include the execution's
        # whole starting minute, but never an earlier minute (which could revive a prior run).
        execution_minute = started_at.replace(second=0, microsecond=0)
        if sent_at is None or sent_at < execution_minute or sent_at > delivered_at:
            continue
        for key in ("id", "companion_id"):
            value = entry.get(key)
            if isinstance(value, str) and value and value not in ids:
                ids.append(value)
    return ids


def _read_receipts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _draft_documents(drafts_dir: Path) -> Iterable[tuple[Path, Any, list]]:
    for path in sorted(drafts_dir.glob("drafts-*.json")):
        try:
            document = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        drafts = document.get("insights") if isinstance(document, dict) else document
        if isinstance(drafts, list):
            yield path, document, drafts


def _project_receipt(drafts_dir: Path, receipt: dict) -> list[str]:
    """Project a confirmed receipt onto matching drafts, idempotently.

    The JSONL receipt is written first and remains authoritative if this
    best-effort projection is interrupted.  A repeated call therefore repairs
    the projection without appending a duplicate receipt.
    """
    wanted = set(receipt.get("insight_ids") or [])
    projected: list[str] = []
    for path, document, drafts in _draft_documents(drafts_dir):
        changed = False
        for draft in drafts:
            if not isinstance(draft, dict) or draft.get("id") not in wanted:
                continue
            receipt_ids = draft.setdefault("delivery_receipt_ids", [])
            if not isinstance(receipt_ids, list):
                receipt_ids = []
                draft["delivery_receipt_ids"] = receipt_ids
            if receipt["receipt_id"] not in receipt_ids:
                receipt_ids.append(receipt["receipt_id"])
            prior_at = _parse_time(draft.get("delivered_at"))
            receipt_at = _parse_time(receipt["delivered_at"])
            if prior_at is None or (receipt_at is not None and receipt_at < prior_at):
                draft["delivered_at"] = receipt["delivered_at"]
            draft["delivered"] = True
            projected.append(draft["id"])
            changed = True
        if changed:
            _atomic_write_json(path, document)
    return projected


def _receipt_id(execution_id: str, platform: str, chat_id: str, thread_id: Optional[str], message_id: str) -> str:
    identity = "\0".join((execution_id, platform, chat_id, thread_id or "", message_id))
    return "cron-delivery-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def record_confirmed_delivery(
    job: dict,
    *,
    platform: str,
    chat_id: str,
    thread_id: Optional[str],
    message_id: Any,
    content: str,
    transport: str,
) -> Optional[dict]:
    """Attribute current-run insight intent after confirmed platform delivery.

    Returns the receipt, or None when disabled/not safely attributable.  All
    errors are contained: attribution is post-delivery bookkeeping and must
    never turn an already-sent message into a delivery failure or retry.
    """
    config = job.get("insight_attribution")
    if not isinstance(config, dict) or config.get("enabled") is not True:
        return None
    if message_id is None or not str(message_id).strip():
        return None
    try:
        started_at = _execution_started_at(job)
        if started_at is None:
            logger.warning(
                "Job '%s': insight attribution skipped; execution start is unavailable",
                job.get("id"),
            )
            return None
        delivered_at_dt = dt.datetime.now(dt.UTC)
        delivered_at = delivered_at_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        sent_ids_path, drafts_dir, receipts_path = _paths(job)
        insight_ids = _candidate_ids(sent_ids_path, started_at, delivered_at_dt)
        if not insight_ids:
            logger.info("Job '%s': no current-run insight intent to attribute", job.get("id"))
            return None
        execution_id = str(job["execution_id"])
        receipt = {
            "schema": SCHEMA,
            "receipt_id": _receipt_id(
                execution_id, platform, str(chat_id), thread_id, str(message_id)
            ),
            "execution_id": execution_id,
            "job_id": str(job.get("id") or ""),
            "insight_ids": insight_ids,
            "platform": platform,
            "chat_id": str(chat_id),
            "thread_id": str(thread_id) if thread_id is not None else None,
            "message_id": str(message_id),
            "transport": transport,
            "content_sha256": hashlib.sha256((content or "").encode("utf-8")).hexdigest(),
            "delivered_at": delivered_at,
        }
        lock_path = receipts_path.with_suffix(receipts_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                existing = {
                    row.get("receipt_id"): row for row in _read_receipts(receipts_path)
                }
                prior = existing.get(receipt["receipt_id"])
                if prior is None:
                    _append_jsonl(receipts_path, receipt)
                else:
                    # The durable row is authoritative if a replay observes changed intent files.
                    receipt = prior
                projected = set(_project_receipt(drafts_dir, receipt))
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        missing = [value for value in insight_ids if value not in projected]
        if missing:
            logger.warning(
                "Job '%s': delivery receipt recorded but %d insight id(s) had no matching draft",
                job.get("id"), len(missing),
            )
        logger.info(
            "Job '%s': attributed %d insight id(s) to %s:%s message_id=%s",
            job.get("id"), len(insight_ids), platform, chat_id, message_id,
        )
        return receipt
    except Exception as exc:
        logger.warning(
            "Job '%s': post-delivery insight attribution failed: %s",
            job.get("id"), exc, exc_info=True,
        )
        return None
