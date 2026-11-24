"""Local terminal guards for Python heredocs and scratch-script syntax.

Long Python heredocs are fragile in agent-issued shell commands. Keep short probes
convenient, but move substantial code into a real scratch file and compile it before
any foreground process is spawned.
"""

from __future__ import annotations

import os
import py_compile
import re
from pathlib import Path


_PYTHON_HEREDOC_START = re.compile(
    r"\bpython(?:3(?:\.\d+)?)?\b[^\n]*?<<-?\s*(?P<quote>['\"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)[^\n]*\n"
)
_PY_PATH = re.compile(r"(?P<path>(?:/[^\s'\"]+|~[^\s'\"]*|\$HOME[^\s'\"]*)\.py)")


def reject_python_heredoc(command: str) -> str | None:
    """Return a block reason for Python heredocs longer than three body lines."""
    if not command:
        return None
    for match in _PYTHON_HEREDOC_START.finditer(command):
        tag = match.group("tag")
        remainder = command[match.end():]
        lines = remainder.splitlines()
        body = []
        terminated = False
        for line in lines:
            if line.strip() == tag:
                terminated = True
                break
            body.append(line)
        if not terminated or len(body) > 3:
            return (
                "Python heredocs over three lines (including unterminated heredocs) are blocked. "
                "Write the probe with write_file under /Users/jkw/.hermes/cache/scratch/, "
                "then run python3 -m py_compile before executing it."
            )
    return None


def require_scratch_py_compile(command: str) -> str | None:
    """Compile every referenced scratch .py file before the shell can execute it."""
    if not command:
        return None
    from hermes_constants import get_hermes_home
    scratch_root = get_hermes_home() / "cache" / "scratch"
    for match in _PY_PATH.finditer(command):
        raw = match.group("path")
        expanded = os.path.expanduser(os.path.expandvars(raw))
        candidate = Path(expanded)
        try:
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(scratch_root.resolve())
        except (OSError, ValueError):
            continue
        if not resolved.is_file():
            return f"Scratch Python script does not exist: {resolved}. Write it with write_file first."
        try:
            py_compile.compile(str(resolved), doraise=True)
        except py_compile.PyCompileError as exc:
            return f"Scratch Python script failed py_compile before execution: {resolved}: {exc.msg}"
    return None
