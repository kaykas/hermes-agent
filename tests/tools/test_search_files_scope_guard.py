"""search_files scope guard, end to end through ``search_tool``.

- Broad roots ($HOME without a file_glob, anything above $HOME, newest-first file
  scans over $HOME) are refused before any search engine process is spawned.
- Punctuation-heavy literal content patterns are searched as fixed strings.
- Unproductive re-scoped retries of one pattern are capped within a streak.
"""
import json
import uuid

import pytest

from tools import file_tools
from tools import file_operations
from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations
from tools.file_operations_common import SearchResult
from tools.file_tools_read_tracking import _read_tracker, notify_other_tool_call
from tools.file_tools_search_guard import SEARCH_RETRY_CAP, looks_like_punctuation_literal


@pytest.fixture
def task_id():
    tid = f"scope-guard-{uuid.uuid4().hex}"
    yield tid
    _read_tracker.pop(tid, None)


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = home / "work" / "proj"
    project.mkdir(parents=True)
    (project / "calls.py").write_text("obj.method()\nobjXmethod\nfoo(bar\ncolor colour\n")
    monkeypatch.setattr(file_operations, "_HOME", str(home))
    return home


@pytest.fixture
def ops(home, monkeypatch):
    ops = ShellFileOperations(LocalEnvironment(cwd=str(home)))
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda _tid="default": ops)
    return ops


@pytest.fixture
def spawned(ops, monkeypatch):
    """Every search-engine launch (rg native/bounded, shell pipelines) is recorded."""
    calls = []
    for name in ("_run_rg_bounded", "_run_rg_native", "_dispatch_search"):
        real = getattr(ops, name)

        def spy(*a, _real=real, _name=name, **k):
            calls.append(_name)
            return _real(*a, **k)
        monkeypatch.setattr(ops, name, spy)
    return calls


def _search(task_id, **kw):
    return json.loads(file_tools.search_tool(task_id=task_id, **kw))


# --- broad roots ----------------------------------------------------------------

@pytest.mark.platforms("posix")
@pytest.mark.parametrize("kwargs, needle", [
    ({"pattern": "needle", "path": "HOME"}, "file_glob"),
    ({"pattern": "needle", "path": "PARENT"}, "below your home"),
    ({"pattern": "needle", "path": "/"}, "below your home"),
    ({"pattern": "*.py", "target": "files", "path": "HOME", "order": "modified"}, "order='discovery'"),
])
def test_broad_roots_rejected_before_spawning(home, spawned, task_id, kwargs, needle):
    kwargs = dict(kwargs)
    kwargs["path"] = {"HOME": str(home), "PARENT": str(home.parent)}.get(kwargs["path"], kwargs["path"])

    out = _search(task_id, **kwargs)

    assert needle in out["error"]
    assert spawned == []


@pytest.mark.platforms("posix")
def test_scoped_roots_still_search(home, spawned, task_id):
    content = _search(task_id, pattern="colou?r", path=str(home / "work"))
    globbed = _search(task_id, pattern="colou?r", path=str(home), file_glob="*.py")
    newest = _search(task_id, pattern="*.py", target="files", path=str(home / "work"), order="modified")

    assert content["total_count"] == globbed["total_count"] == 1
    assert newest["files"] and newest["files"][0].endswith("calls.py")
    assert "_dispatch_search" in spawned


# --- fixed strings --------------------------------------------------------------

@pytest.mark.parametrize("pattern", ["obj.method()", "foo(bar", "$HOME", "*args", "a[0].b", "x || y"])
def test_punctuation_heavy_literals_detected(pattern):
    assert looks_like_punctuation_literal(pattern)


@pytest.mark.parametrize("pattern", ["needle", "config.yaml", "colou?r", r"def \w+\(", "^import",
                                     "foo|bar", "[a-z]+_id", ".*TODO", "x{2,3}", "(?i)hermes"])
def test_regexes_and_plain_words_left_alone(pattern):
    assert not looks_like_punctuation_literal(pattern)


@pytest.mark.platforms("posix")
def test_auto_fixed_strings_matches_only_the_literal(home, ops, task_id):
    out = _search(task_id, pattern="obj.method()", path=str(home / "work"))

    assert out["fixed_strings"] is True and "fixed_strings=false" in out["_fixed_strings"]
    assert out["total_count"] == 1 and "obj.method()" in json.dumps(out)


@pytest.mark.platforms("posix")
def test_invalid_regex_literal_is_searched_not_errored(home, ops, task_id):
    out = _search(task_id, pattern="foo(bar", path=str(home / "work"))

    assert "error" not in out and out["total_count"] == 1


@pytest.mark.platforms("posix")
def test_explicit_fixed_strings_false_keeps_regex(home, ops, task_id):
    out = _search(task_id, pattern="obj.method()", path=str(home / "work"), fixed_strings=False)

    assert "fixed_strings" not in out and out["total_count"] == 2


@pytest.mark.platforms("posix")
def test_fixed_strings_escaping_is_valid_for_grep_fallback(home, ops, task_id, monkeypatch):
    monkeypatch.setattr(ops, "_has_command", lambda cmd: cmd != "rg")

    out = _search(task_id, pattern="obj.method()", path=str(home / "work"), fixed_strings=True)

    assert out["total_count"] == 1


def test_handler_forwards_fixed_strings(home, ops, task_id):
    out = json.loads(file_tools._handle_search_files(
        {"pattern": "colou?r", "path": str(home / "work"), "fixed_strings": True}, task_id=task_id))

    assert out["fixed_strings"] is True and out["total_count"] == 0


# --- same-turn retry cap --------------------------------------------------------

class _FakeOps:
    def __init__(self, total=0):
        self.total, self.calls = total, []

    def search(self, **kw):
        self.calls.append(kw["path"])
        return SearchResult(total_count=self.total, files=["hit"] if self.total else [])

    def hidden_match_counts(self, pattern, path, file_glob):
        # v0.23.5 asks how many matches sit in hidden/ignored paths after an empty search;
        # None means "unknown", which leaves the hint out, as on a host without ripgrep.
        return None


def _fake(monkeypatch, total=0):
    fake = _FakeOps(total)
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda _tid="default": fake)
    return fake


def test_unproductive_rescoped_retries_are_capped(tmp_path, task_id, monkeypatch):
    fake = _fake(monkeypatch)
    for i in range(SEARCH_RETRY_CAP):
        assert "error" not in _search(task_id, pattern="needle", path=str(tmp_path / f"p{i}"))

    blocked = _search(task_id, pattern="needle", path=str(tmp_path / "other"), file_glob="*.md")

    assert blocked["error"].startswith("BLOCKED") and blocked["unproductive_attempts"] == SEARCH_RETRY_CAP
    assert len(fake.calls) == SEARCH_RETRY_CAP
    assert "error" not in _search(task_id, pattern="different", path=str(tmp_path))


def test_retry_cap_resets_when_another_tool_runs(tmp_path, task_id, monkeypatch):
    fake = _fake(monkeypatch)
    for i in range(SEARCH_RETRY_CAP):
        _search(task_id, pattern="needle", path=str(tmp_path / f"p{i}"))

    notify_other_tool_call(task_id)

    assert "error" not in _search(task_id, pattern="needle", path=str(tmp_path / "again"))
    assert len(fake.calls) == SEARCH_RETRY_CAP + 1


def test_productive_search_clears_retry_count(tmp_path, task_id, monkeypatch):
    fake = _fake(monkeypatch)
    for i in range(SEARCH_RETRY_CAP - 1):
        _search(task_id, pattern="needle", path=str(tmp_path / f"p{i}"))
    fake.total = 1
    _search(task_id, pattern="needle", path=str(tmp_path / "hit"))
    fake.total = 0

    for i in range(SEARCH_RETRY_CAP):
        assert "error" not in _search(task_id, pattern="needle", path=str(tmp_path / f"q{i}"))


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
