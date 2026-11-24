"""Preflight guard: Hermes multiplex profiles live under ``<hermes-root>/profiles/<name>/``.

Hermes-hosted fleet agents (nova2, cedar, ...) share names with the OpenClaw fleet, so
models keep probing ``~/.openclaw/agents/<name>/sessions/`` — a tree that never exists
for a Hermes profile — and spin in permission/syntax retry loops. The terminal and file
tools call this guard before executing; a hit is refused with the correct Hermes path so
the next call lands on the real profile home instead of retrying the dead one.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

MULTIPLEX_PROFILES = frozenset({
    "nova2", "cedar", "heath", "icculus", "jeter", "halo", "carlton", "hendrick", "pippin",
})

# ``~``, ``$HOME``, ``${HOME}`` or an absolute home prefix all end in this segment.
_OPENCLAW_AGENT_RE = re.compile(r"\.openclaw/agents/([A-Za-z0-9_.-]+)(/[^\s'\"`;|&)]*)?")


def hermes_profiles_root() -> Path:
    from hermes_constants import get_default_hermes_root
    return get_default_hermes_root() / "profiles"


def openclaw_agents_root() -> Path:
    return Path.home() / ".openclaw" / "agents"


def profile_home(profile: str, *, profiles_root: Optional[Path] = None) -> Path:
    """The Hermes home for a multiplex *profile*."""
    return (profiles_root or hermes_profiles_root()) / profile


def _refusal(profile: str, rest: str, profiles_root: Optional[Path]) -> str:
    target = profile_home(profile, profiles_root=profiles_root)
    suggestion = f"{target}{rest}" if rest else f"{target}/"
    return (
        f"Blocked: '{profile}' is a Hermes multiplex profile; it has no "
        f"~/.openclaw/agents/{profile}/ tree. Its home (config, sessions, memories, "
        f"skills) is {target}/. Use {suggestion} instead."
    )


def check_path(path: str, *, profiles_root: Optional[Path] = None,
               openclaw_root: Optional[Path] = None) -> Optional[str]:
    """Refusal text when *path* targets ``~/.openclaw/agents/<multiplex profile>``, else None."""
    if not isinstance(path, str) or not path:
        return None
    root = os.path.normpath(str(openclaw_root or openclaw_agents_root()))
    candidate = os.path.normpath(os.path.expanduser(os.path.expandvars(path)))
    if candidate != root and not candidate.startswith(root + os.sep):
        # Relative or differently-anchored spellings still carry the segment.
        return check_command(path, profiles_root=profiles_root)
    parts = Path(candidate).relative_to(root).parts
    if not parts or parts[0] not in MULTIPLEX_PROFILES:
        return None
    rest = "/" + "/".join(parts[1:]) if len(parts) > 1 else ""
    return _refusal(parts[0], rest, profiles_root)


def check_command(command: str, *, profiles_root: Optional[Path] = None) -> Optional[str]:
    """Refusal text when a shell *command* references a multiplex profile's OpenClaw tree."""
    if not isinstance(command, str):
        return None
    for match in _OPENCLAW_AGENT_RE.finditer(command):
        profile = match.group(1)
        if profile in MULTIPLEX_PROFILES:
            return _refusal(profile, match.group(2) or "", profiles_root)
    return None
