#!/usr/bin/env python3
"""Fail before a git/Python command uses a missing file pathspec.

This checker deliberately validates only explicit pathspecs supplied by the caller.
It does not parse shell command strings or guess whether a branch name is a path.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class MissingPathspec:
    supplied: str
    resolved: str


def _is_ref_only(pathspec: str) -> bool:
    """Return True for the ref-only forms this tool intentionally ignores."""
    return not any(marker in pathspec for marker in ("/", ".", "*", "?", "[", "~"))


def check_pathspecs(pathspecs: list[str], cwd: str) -> list[MissingPathspec]:
    """Return every explicit file pathspec that does not resolve to an existing path."""
    base = os.path.abspath(os.path.expanduser(cwd))
    missing: list[MissingPathspec] = []
    for supplied in pathspecs:
        if not supplied or _is_ref_only(supplied):
            continue
        expanded = os.path.expanduser(supplied)
        resolved = expanded if os.path.isabs(expanded) else os.path.join(base, expanded)
        if glob.has_magic(expanded):
            matches = glob.glob(resolved, recursive=True)
            exists = any(os.path.exists(match) for match in matches)
        else:
            exists = os.path.exists(resolved)
        if not exists:
            missing.append(MissingPathspec(supplied, os.path.abspath(resolved)))
    return missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pathspec", action="append", default=[], help="File pathspec to validate; repeatable")
    parser.add_argument("--cwd", default=os.getcwd(), help="Base directory for relative pathspecs")
    args = parser.parse_args(argv)
    missing = check_pathspecs(args.pathspec, args.cwd)
    for item in missing:
        print(f"pathspec does not exist: {item.supplied} (resolved: {item.resolved})", file=sys.stderr)
    if missing:
        print(f"preflight-pathspec-existence: {len(missing)} missing pathspec(s); command not spawned", file=sys.stderr)
        return 1
    print(f"preflight-pathspec-existence: OK ({len(args.pathspec)} argument(s) inspected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
