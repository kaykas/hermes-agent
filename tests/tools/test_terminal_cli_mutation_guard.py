"""Wiring tests for the terminal CLI-mutation preflight."""
import json

import pytest

from tools import cli_mutation_guard
from tools.terminal_tool import _Rejected, _pre_exec_block


def _kwargs():
    return {"env": None, "env_type": "local", "cwd": "/tmp", "workdir": None, "session_key": "test"}


def test_non_mutation_command_skips_preflight():
    assert cli_mutation_guard.cli_mutation_block("python3 script.py") is None


def test_terminal_rejects_known_mutation_before_execution(monkeypatch):
    monkeypatch.setattr("tools.terminal_tool.gateway_lifecycle_block", lambda **kwargs: None)
    monkeypatch.setattr("tools.terminal_tool.self_repo_block", lambda **kwargs: None)
    monkeypatch.setattr(cli_mutation_guard, "cli_mutation_block", lambda command, cwd=None: "READ-only repository")
    with pytest.raises(_Rejected) as caught:
        _pre_exec_block("git push origin main", **_kwargs())
    result = json.loads(str(caught.value))
    assert result["status"] == "blocked"
    assert result["retryable"] is False
    assert "READ-only" in result["error"]


def test_terminal_allows_mutation_when_preflight_passes(monkeypatch):
    monkeypatch.setattr("tools.terminal_tool.gateway_lifecycle_block", lambda **kwargs: None)
    monkeypatch.setattr("tools.terminal_tool.self_repo_block", lambda **kwargs: None)
    monkeypatch.setattr(cli_mutation_guard, "cli_mutation_block", lambda command, cwd=None: None)
    _pre_exec_block("git push origin main", **_kwargs())
