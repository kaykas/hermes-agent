#!/usr/bin/env python3
"""
gateway/outbound_reply_sanitizer.py — Pure, importable sanitizer for outbound
gateway replies so literal /Users/<user>/.hermes/config.yaml and
/Users/<user>/.openclaw/openclaw.json strings are rewritten to their ~/ form
before platform delivery policy evaluation.
"""

from __future__ import annotations

import re

_CONFIG_PATH_RE = re.compile(
    r"/Users/[^/\s'\"`]+/(\.hermes/config\.yaml|\.openclaw/openclaw\.json)"
)


def sanitize_outbound_reply(text: str) -> str:
    """Rewrite literal absolute Hermes/OpenClaw config paths to ~/ form."""
    if not text:
        return text
    return _CONFIG_PATH_RE.sub(r"~/\1", text)
