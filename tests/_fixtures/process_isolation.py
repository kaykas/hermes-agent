"""Keep host process path discovery inside the test's process tree."""

from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolate_process_discovery(_hermetic_environment, request, monkeypatch):
    """Deny path inspection of unrelated macOS processes during tests.

    Holder, worktree, and browser scans enumerate every process, then dereference command
    lines, open files, working directories, or environments. On a developer host those can
    resolve paths in the operator's unrelated, live Hermes home. Preserve the process listing
    itself: only these path-returning methods behave as though the unrelated process were
    inaccessible. Test-created descendants remain fully visible, and test-installed psutil
    doubles still replace these wrappers.

    A test that deliberately reparents its own child can opt out with
    ``@pytest.mark.real_process_discovery``: ancestry alone cannot identify that orphan as
    fixture-owned once init adopts it.
    """
    if sys.platform != "darwin" or request.node.get_closest_marker("real_process_discovery"):
        return

    import psutil

    process_class = psutil.Process
    real_ppid = process_class.ppid
    real_cmdline = process_class.cmdline
    real_cwd = process_class.cwd
    real_environ = process_class.environ
    real_exe = process_class.exe
    real_open_files = process_class.open_files
    test_pid = os.getpid()

    def test_owned(process) -> bool:
        pid = process.pid
        seen = set()
        while pid > 0 and pid not in seen:
            if pid == test_pid:
                return True
            seen.add(pid)
            try:
                process = process_class(pid)
                pid = real_ppid(process)
            except (psutil.Error, OSError):
                return False
        return False

    def cmdline(process):
        if not test_owned(process):
            raise psutil.AccessDenied(process.pid)
        return real_cmdline(process)

    def cwd(process):
        if not test_owned(process):
            raise psutil.AccessDenied(process.pid)
        return real_cwd(process)

    def environ(process):
        if not test_owned(process):
            raise psutil.AccessDenied(process.pid)
        return real_environ(process)

    def open_files(process):
        if not test_owned(process):
            raise psutil.AccessDenied(process.pid)
        return real_open_files(process)

    def exe(process):
        if not test_owned(process):
            raise psutil.AccessDenied(process.pid)
        return real_exe(process)

    monkeypatch.setattr(process_class, "cmdline", cmdline)
    monkeypatch.setattr(process_class, "cwd", cwd)
    monkeypatch.setattr(process_class, "environ", environ)
    monkeypatch.setattr(process_class, "open_files", open_files)
    monkeypatch.setattr(process_class, "exe", exe)
