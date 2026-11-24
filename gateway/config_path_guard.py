"""Outbound configuration-path normalization and attachment denial."""

import os
import re


_CONFIG_PATH_RE = re.compile(
    r"/(?:Users|home)/[^/\s'\"`]+/"
    r"(\.hermes/config\.ya?ml|\.openclaw/[^/\s'\"`]+\.(?:ya?ml|json)|\.claude/\.mcp\.json)"
    r"(?=$|[\s`'\"*_.,;:)}\]])"
)
_PROTECTED_ATTACHMENT_RE = re.compile(
    r"^(?:/(?:Users|home)/[^/]+|~)/"
    r"(?:\.hermes/config\.ya?ml|\.openclaw/[^/]+\.(?:ya?ml|json)|\.claude/\.mcp\.json)/?$"
)


def redact_config_paths(text: str) -> str:
    """Rewrite protected absolute config paths to non-dispatching home-relative text."""
    if not text:
        return text
    return _CONFIG_PATH_RE.sub(r"~/\1", str(text))


def is_protected_config_attachment(path: str) -> bool:
    """Return whether a local path names configuration that must never be attached."""
    if not path:
        return False
    candidate = str(path).strip().removeprefix("file://")
    return bool(_PROTECTED_ATTACHMENT_RE.fullmatch(candidate))
