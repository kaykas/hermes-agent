#!/usr/bin/env python3
"""
mcp_wiki_filesystem_gate.py — Pre-call scope guard for mcp_wiki_filesystem_* tools.

Reads a Hermes shell-hook JSON payload from stdin.
Blocks any mcp_wiki_filesystem_* tool call whose target path does not fall
within /Users/jkw/wiki/ after normalization (symlink resolution, .., . removal).

Wire this in ~/.hermes/config.yaml:

    hooks:
      pre_tool_call:
        - matcher: "mcp_wiki_filesystem_.*"
          command: "/Users/jkw/.hermes/tools/mcp_wiki_filesystem_gate.py"

Output contract (Hermes shell-hook JSON wire protocol):
  - Block:  {"action": "block", "message": "<reason>"}
  - Allow:  (empty stdout or non-JSON)
"""

import json
import os
import sys

ALLOWED_ROOT = "/Users/jkw/wiki/"

REPLACEMENT_HINT = (
    "use read_file / write_file / patch for paths outside /Users/jkw/wiki/. "
    "mcp_wiki_filesystem_* is scoped exclusively to /Users/jkw/wiki/."
)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed payload — allow through; Hermes logs the hook warning.
        sys.exit(0)

    tool_name: str = payload.get("tool_name") or ""
    tool_input: dict = payload.get("tool_input") or {}

    # Only act on mcp_wiki_filesystem_* tools (matcher in config.yaml already
    # pre-filters, but guard here too for defence-in-depth).
    if not tool_name.startswith("mcp_wiki_filesystem_"):
        sys.exit(0)

    # Extract path argument — the MCP filesystem tools use "path" as the key.
    path: str = tool_input.get("path") or ""

    # Normalize path: resolve symlinks, convert to absolute, remove .. and .
    try:
        normalized_path = os.path.normpath(os.path.realpath(os.path.expanduser(path)))
    except (OSError, ValueError):
        # If normalization fails (e.g., broken symlink), block to be safe.
        decision = {
            "action": "block",
            "message": (
                f"mcp_wiki_filesystem_* path validation failed: could not normalize '{path}'. "
                f"{REPLACEMENT_HINT}"
            ),
        }
        print(json.dumps(decision))
        sys.exit(0)

    # Normalize allowed root for comparison.
    allowed_root_normalized = os.path.normpath(ALLOWED_ROOT.rstrip("/"))

    # Allow: normalized path is inside the permitted root, or IS the root directory itself.
    if normalized_path.startswith(allowed_root_normalized + "/") or normalized_path == allowed_root_normalized:
        sys.exit(0)

    # Block: path is outside /Users/jkw/wiki/.
    decision = {
        "action": "block",
        "message": (
            f"mcp_wiki_filesystem_* out of scope: path '{path}' is outside "
            f"the allowed root {ALLOWED_ROOT!r}. {REPLACEMENT_HINT}"
        ),
    }
    print(json.dumps(decision))
    sys.exit(0)


if __name__ == "__main__":
    main()
