import json
from types import SimpleNamespace

import tools.terminal_tool as terminal_module
from tools.terminal_timeout_retry import (
    _reset_for_tests, record_outcome, reject_unchanged_timeout,
)


def setup_function():
    _reset_for_tests()


def note(command="slow search", workdir="/repo", timeout=None, code=124):
    record_outcome(task_id="turn-a", command=command, workdir=workdir,
                   timeout=timeout, result_json=json.dumps({"exit_code": code}))


def retry(command="slow search", workdir="/repo", timeout=None, background=False):
    return reject_unchanged_timeout(task_id="turn-a", command=command, workdir=workdir,
                                    timeout=timeout, background=background)


def test_identical_default_180_retry_is_blocked_without_executing():
    assert retry() is None
    note()
    message = retry()
    assert message and "BLOCKED" in message and "background=true" in message


def test_explicit_180_is_also_blocked():
    note(timeout=180)
    assert retry(timeout=180)


def test_narrowed_command_or_workdir_is_allowed():
    note()
    assert retry(command="slow search --scope src") is None
    assert retry(workdir="/repo/src") is None


def test_longer_timeout_or_background_is_allowed():
    note()
    assert retry(timeout=600) is None
    assert retry(background=True) is None


def test_non_timeout_outcome_creates_no_block_and_success_clears_one():
    note(code=1)
    assert retry() is None
    note()
    note(code=0)
    assert retry() is None


def test_state_is_task_scoped():
    note()
    assert reject_unchanged_timeout(task_id="turn-b", command="slow search",
                                    workdir="/repo", timeout=None, background=False) is None


def test_terminal_wrapper_rejects_before_environment_or_process_starts(monkeypatch):
    note()
    plan = SimpleNamespace(effective_task_id="turn-a", cwd="/repo", auto_background_command=None)
    monkeypatch.setattr(terminal_module, "_plan_execution", lambda *a, **k: plan)

    def forbidden(*args, **kwargs):
        raise AssertionError("environment/process setup must not start")

    monkeypatch.setattr(terminal_module, "_acquire_env", forbidden)
    result = json.loads(terminal_module.terminal_tool("slow search", task_id="turn-a", workdir="/repo"))
    assert result["status"] == "blocked"
    assert result["exit_code"] == 124
    assert "already timed out" in result["error"]
