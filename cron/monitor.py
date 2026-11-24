"""Monitor-mode cron support — hash-suppressed change detection.

A monitor job attaches a cheap source (``monitor_script`` / ``monitor_url``) to an LLM cron job.
Each tick runs the source FIRST and hashes its EXACT output bytes (no timestamp/whitespace
normalization — scripts must emit stable output) against the hash from the last agent-triggering
tick: unchanged → agent run suppressed (silent ``no_change`` run); changed/first run → a "MONITOR
CHANGE DETECTED" block (capped unified diff + new output) is injected into the prompt; source
failure → an ERROR, never a change, and the stored hash is left untouched. State:
``job["monitor_state"]`` in jobs.json (hash + last_changed_at) and
``OUTPUT_DIR/<job_id>/monitor_last_output.txt`` (for the diff).
"""

from __future__ import annotations

import difflib
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Prompt-injection caps: unified diff, and new-output block (mirrors the 8k context_from truncation
# in cron/scheduler.py). Then bounded-GET limits for monitor_url sources.
MAX_DIFF_CHARS = 4000
MAX_OUTPUT_CHARS = 8000
URL_TIMEOUT_SECONDS = 30
MAX_URL_BYTES = 262_144  # 256 KiB
MAX_URL_REDIRECTS = 5

_SNAPSHOT_FILENAME = "monitor_last_output.txt"


@dataclass
class MonitorOutcome:
    """Result of one monitor-source evaluation."""

    ok: bool
    changed: bool = False
    first_run: bool = False
    context_block: Optional[str] = None
    error: Optional[str] = None


def hash_monitor_output(output: str) -> str:
    """Hash the monitor output as exact UTF-8 bytes (no normalization)."""
    return hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest()


def build_monitor_diff(old: str, new: str) -> str:
    """Unified diff of old vs new monitor output, capped at MAX_DIFF_CHARS."""
    diff = "\n".join(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), fromfile="previous", tofile="current", lineterm="",
        )
    )
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... [diff truncated]"
    return diff


def _snapshot_path(job_id: str):
    from cron.jobs import _job_output_dir

    return _job_output_dir(job_id) / _SNAPSHOT_FILENAME


def _read_last_output(job_id: str) -> str:
    try:
        path = _snapshot_path(job_id)
        if path.exists():
            return path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        logger.warning("Monitor: failed to read last output for %r: %s", job_id, exc)
    return ""


def _write_last_output(job_id: str, output: str) -> None:
    try:
        from cron.jobs import _ensure_cron_dir

        path = _snapshot_path(job_id)
        _ensure_cron_dir(path.parent)
        path.write_text(output, encoding="utf-8")
    except Exception as exc:
        logger.warning("Monitor: failed to persist last output for %r: %s", job_id, exc)


def _fetch_monitor_url(url: str) -> tuple[bool, str]:
    """Bounded GET of a monitor URL. Returns (ok, body-or-error).

    The URL can come from the agent's own cronjob tool call and the body is fed back into the
    agent's prompt, so every hop (initial URL and each redirect target) passes the shared SSRF
    policy, and the connection itself goes through the connect-time-guarded client.
    """
    from urllib.parse import urljoin

    from tools.url_safety import create_ssrf_safe_client, is_safe_url

    current_url = str(url)
    if not current_url.lower().startswith(("http://", "https://")):
        return False, f"monitor_url must be http(s): {url!r}"
    try:
        with create_ssrf_safe_client(timeout=URL_TIMEOUT_SECONDS, follow_redirects=False,
                                     headers={"User-Agent": "hermes-cron-monitor",
                                              "Accept-Encoding": "identity"}) as client:
            for _ in range(MAX_URL_REDIRECTS + 1):
                if not is_safe_url(current_url):
                    return False, ("monitor_url blocked: private/internal or unsafe destination "
                                   "(intranet monitors need security.allow_private_urls: true)")
                with client.stream("GET", current_url) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            return False, "monitor_url redirect without a Location header"
                        current_url = urljoin(current_url, location)
                        continue
                    resp.raise_for_status()
                    body = bytearray()
                    # Raw bytes, as urllib read them: decoding happens before this cap sees a
                    # chunk, and one read of a hostile br/gzip body inflates to GBs.
                    for chunk in resp.iter_raw():
                        body += chunk
                        if len(body) > MAX_URL_BYTES:
                            break
                return True, bytes(body[:MAX_URL_BYTES]).decode("utf-8", errors="replace")
        return False, f"monitor_url exceeded {MAX_URL_REDIRECTS} redirects"
    except Exception as exc:
        return False, f"monitor_url fetch failed: {exc}"


def _field(job: dict, key: str) -> str:
    return (job.get(key) or "").strip()


def _run_monitor_source(job: dict) -> tuple[bool, str]:
    """Run the job's monitor source (script or URL). Returns (ok, output)."""
    monitor_script = _field(job, "monitor_script")
    if monitor_script:
        # Same containment + interpreter rules as the existing `script` field.
        from cron.scheduler_script import _run_job_script

        return _run_job_script(monitor_script, workdir=_field(job, "workdir") or None,
                               interpreter=job.get("interpreter"))
    monitor_url = _field(job, "monitor_url")
    if monitor_url:
        return _fetch_monitor_url(monitor_url)
    return False, "monitor job has neither monitor_script nor monitor_url"


def job_has_monitor(job: dict) -> bool:
    return bool(_field(job, "monitor_script") or _field(job, "monitor_url"))


def check_monitor(job: dict) -> MonitorOutcome:
    """Run the monitor source and decide whether the agent should run.

    On change (or first run) the new hash + snapshot are persisted BEFORE the agent runs — detection
    time is the state boundary. A failed agent run gets up to three attempts for the same output;
    success latches it, while the final failure suppresses it until the content changes.
    Source failures set only a diagnostic marker and never consume an agent attempt.
    """
    job_id = str(job.get("id") or "")
    raw_state = job.get("monitor_state")
    ok, output = _run_monitor_source(job)
    if not ok:
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        state["source_failure_active"] = True
        try:
            from cron.jobs import update_job

            update_job(job_id, {"monitor_state": state})
        except Exception as exc:
            logger.warning("Monitor: failed to record source failure for %r: %s", job_id, exc)
        return MonitorOutcome(ok=False, error=output)

    new_hash = hash_monitor_output(output)
    retry_state = raw_state if isinstance(raw_state, dict) else {}
    last_hash = retry_state.get("last_output_hash")
    retry_count = int(retry_state.get("retry_count") or 0)
    same_output = last_hash is not None and new_hash == last_hash
    retry_same_output = bool(
        same_output
        and retry_state.get("agent_attempt_required")
        and 0 < retry_count < 3
    )
    if same_output and not retry_same_output:
        return MonitorOutcome(ok=True, changed=False)

    first_run = last_hash is None
    old_output = "" if first_run else _read_last_output(job_id)

    shown_output = output
    if len(shown_output) > MAX_OUTPUT_CHARS:
        shown_output = shown_output[:MAX_OUTPUT_CHARS] + "\n... [output truncated]"

    current = f"### Current output\n\n```\n{shown_output}\n```"
    if retry_same_output:
        context_block = (
            f"## MONITOR RETRY (attempt {retry_count + 1} of 3)\n\n"
            "The previous agent attempt failed for this unchanged output. "
            "Complete the same work; do not treat this as new content.\n\n" + current
        )
    elif first_run:
        context_block = (
            "## Monitor Baseline (first run)\n\n"
            "This is the first observation of the monitored source — there is "
            "no previous output to diff against.\n\n" + current
        )
    else:
        diff = build_monitor_diff(old_output, output)
        context_block = (
            "## MONITOR CHANGE DETECTED\n\n"
            "The monitored source's output changed since the last run.\n\n"
            f"### Diff (previous → current)\n\n```diff\n{diff}\n```\n\n" + current
        )

    _persist_monitor_state(
        job_id, new_hash, output, retry_count=retry_count if retry_same_output else 0
    )
    return MonitorOutcome(ok=True, changed=True, first_run=first_run, context_block=context_block)


def _persist_monitor_state(job_id: str, new_hash: str, output: str, *, retry_count: int) -> None:
    from cron.jobs import _hermes_now, update_job

    _write_last_output(job_id, output)
    try:
        update_job(
            job_id,
            {
                "monitor_state": {
                    "last_output_hash": new_hash,
                    "last_changed_at": _hermes_now().isoformat(),
                    "agent_attempt_required": True,
                    "retry_count": retry_count,
                }
            },
        )
    except Exception as exc:
        logger.warning("Monitor: failed to persist state for %r: %s", job_id, exc)


def record_monitor_agent_result(job: dict, success: bool) -> None:
    """Resolve one changed-output agent attempt inside the jobs write lock.

    The first two failures leave a pending retry for the next tick. The third
    latches the hash, preventing an unchanging failure from retrying forever.
    A source failure is not an agent attempt and leaves the budget unchanged.
    """
    state = job.get("monitor_state")
    if not isinstance(state, dict):
        return
    if state.pop("source_failure_active", False):
        return
    if not state.get("agent_attempt_required"):
        return
    if success:
        state["agent_attempt_required"] = False
        state["retry_count"] = 0
        return
    attempts = int(state.get("retry_count") or 0) + 1
    state["retry_count"] = attempts
    if attempts >= 3:
        state["agent_attempt_required"] = False
