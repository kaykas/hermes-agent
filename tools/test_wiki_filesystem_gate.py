#!/usr/bin/env python3
"""
Test suite for mcp_wiki_filesystem_gate.py scope guard.
Tests path normalization, symlink resolution, and .. removal.
"""

import json
import os
import subprocess
import sys
import tempfile

GATE_SCRIPT = "/Users/jkw/.hermes/tools/mcp_wiki_filesystem_gate.py"
ALLOWED_ROOT = "/Users/jkw/wiki"


def run_gate(tool_name: str, path: str) -> dict:
    """Run the scope guard with a given tool and path, return decision."""
    payload = {
        "tool_name": tool_name,
        "tool_input": {"path": path},
    }
    result = subprocess.run(
        [sys.executable, GATE_SCRIPT],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )

    # Parse output: empty stdout means allow, JSON means block.
    if result.stdout.strip():
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"action": "allow", "reason": "non-JSON output (allow)"}
    return {"action": "allow", "reason": "empty stdout (allow)"}


def test_case(name: str, tool_name: str, path: str, expected_action: str) -> bool:
    """Run a single test case."""
    decision = run_gate(tool_name, path)
    action = decision.get("action", "allow")
    passed = action == expected_action

    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"{status}: {name}")
    print(f"  Path: {path}")
    print(f"  Expected: {expected_action}, Got: {action}")
    if not passed:
        print(f"  Decision: {decision}")
    print()
    return passed


def main():
    print("=" * 70)
    print("mcp_wiki_filesystem_gate.py Scope Guard Test Suite")
    print("=" * 70)
    print()

    results = []

    # === ALLOWED PATHS ===
    print("--- ALLOWED PATHS (should pass through) ---")
    results.append(test_case(
        "Valid: Inside wiki directory",
        "mcp_wiki_filesystem_read_file",
        "/Users/jkw/wiki/somefile.md",
        "allow"
    ))

    results.append(test_case(
        "Valid: Nested in wiki directory",
        "mcp_wiki_filesystem_read_file",
        "/Users/jkw/wiki/subdir/file.txt",
        "allow"
    ))

    results.append(test_case(
        "Valid: Wiki root itself",
        "mcp_wiki_filesystem_list_directory",
        "/Users/jkw/wiki",
        "allow"
    ))

    results.append(test_case(
        "Valid: Wiki root with trailing slash",
        "mcp_wiki_filesystem_list_directory",
        "/Users/jkw/wiki/",
        "allow"
    ))

    # === PATH TRAVERSAL ATTACKS ===
    print("--- PATH TRAVERSAL ATTACKS (should be blocked) ---")
    results.append(test_case(
        "Attack: .. traversal to .open",
        "mcp_wiki_filesystem_read_file",
        "/Users/jkw/wiki/../.open/secret.txt",
        "block"
    ))

    results.append(test_case(
        "Attack: Multiple .. traversal",
        "mcp_wiki_filesystem_read_file",
        "/Users/jkw/wiki/../../Users/jkw/.open/secret.txt",
        "block"
    ))

    results.append(test_case(
        "Attack: Absolute path outside scope",
        "mcp_wiki_filesystem_read_file",
        "/Users/jkw/.open/secret.txt",
        "block"
    ))

    results.append(test_case(
        "Attack: Home directory access",
        "mcp_wiki_filesystem_read_file",
        "~/secret.txt",
        "block"
    ))

    # === SYMLINK RESOLUTION ===
    print("--- SYMLINK RESOLUTION (should detect and block symlink escapes) ---")

    # Create a test symlink that escapes to /tmp
    test_symlink_path = "/Users/jkw/wiki/escape_link"
    try:
        if os.path.exists(test_symlink_path):
            os.unlink(test_symlink_path)
        os.symlink("/tmp", test_symlink_path)

        results.append(test_case(
            "Symlink: Following symlink to /tmp (outside scope)",
            "mcp_wiki_filesystem_read_file",
            test_symlink_path + "/some_file.txt",
            "block"
        ))
    finally:
        if os.path.exists(test_symlink_path):
            os.unlink(test_symlink_path)

    # === EDGE CASES ===
    print("--- EDGE CASES ---")

    results.append(test_case(
        "Edge: Non-wiki tool (should allow through)",
        "some_other_tool",
        "/Users/jkw/.open/secret.txt",
        "allow"
    ))

    results.append(test_case(
        "Edge: Empty path",
        "mcp_wiki_filesystem_read_file",
        "",
        "block"
    ))

    # === SUMMARY ===
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"RESULTS: {passed}/{total} tests passed")
    print("=" * 70)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
