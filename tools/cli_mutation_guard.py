"""Terminal adapter for the fleet's CLI mutation preflight.

Only mutation-capable CLI families enter the preflight. Other shell commands pay
no import or subprocess cost. A known blocked or unverifiable mutation returns a
non-retryable terminal rejection instead of producing a sequence of variants.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import shlex
from pathlib import Path

logger = logging.getLogger(__name__)
_MUTATION_COMMANDS = {"git", "gh", "vercel", "hermes"}
_PREFLIGHT_ENV = "HERMES_CLI_PREFLIGHT"
_DEFAULT_PREFLIGHT = Path.home() / ".hermes" / "scripts" / "hermes-cli-preflight.py"


def _load_preflight():
    path = Path(os.environ.get(_PREFLIGHT_ENV, str(_DEFAULT_PREFLIGHT)))
    spec = importlib.util.spec_from_file_location("hermes_cli_preflight_runtime", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load CLI preflight from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cli_mutation_block(command: str, *, cwd: str | None = None) -> str | None:
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    if not argv or Path(argv[0]).name not in _MUTATION_COMMANDS:
        return None
    try:
        verdict = _load_preflight().check_command(argv, cwd=cwd)
    except Exception as exc:
        # A missing/broken optional guard must not make every mutation impossible.
        logger.warning("CLI mutation preflight could not run: %s", exc)
        return None
    if verdict.code == 0:
        return None
    return f"CLI mutation preflight {verdict.status}: {verdict.reason}"
