"""Same-turn guard for identical foreground commands that timed out at 180s.

The terminal wrapper owns the outcome hook. This module keeps only bounded,
process-local retry state so a later session starts clean.
"""
from __future__ import annotations

import json
import threading

_DEFAULT_TIMEOUT = 180
_MAX_RECORDS = 256
_lock = threading.Lock()
_timeouts: dict[tuple[str, str, str], bool] = {}


def reject_unchanged_timeout(*, task_id: str, command: str, workdir: str,
                             timeout: int | None, background: bool) -> str | None:
    """Return a correction when this exact call already exited 124 at 180s."""
    if background or (timeout is not None and timeout != _DEFAULT_TIMEOUT):
        return None
    key = (task_id or "default", workdir or "", command)
    with _lock:
        if key not in _timeouts:
            return None
    return (
        "BLOCKED: this identical foreground command already timed out after 180s "
        "in this session. Do not retry it unchanged. Narrow the command or its "
        "search scope, set timeout up to 600s, or use background=true."
    )


def record_outcome(*, task_id: str, command: str, workdir: str,
                   timeout: int | None, result_json: str) -> None:
    """Record a default-timeout exit 124; any other outcome clears that command."""
    if timeout is not None and timeout != _DEFAULT_TIMEOUT:
        return
    try:
        result = json.loads(result_json)
        exit_code = result.get("exit_code") if isinstance(result, dict) else None
    except (TypeError, ValueError):
        return
    key = (task_id or "default", workdir or "", command)
    with _lock:
        if exit_code == 124:
            if key not in _timeouts and len(_timeouts) >= _MAX_RECORDS:
                _timeouts.pop(next(iter(_timeouts)))
            _timeouts[key] = True
        else:
            _timeouts.pop(key, None)


def _reset_for_tests() -> None:
    with _lock:
        _timeouts.clear()
