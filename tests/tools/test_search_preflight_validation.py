"""search preflight: query syntax must fit the target mode before any traversal.

Regex tokens in a target='files' glob silently match nothing (a false "absent");
an unscoped content regex over $HOME or wider runs into the rg timeout. Both are
rejected up front with steering, and a missing path reports whether its parent
directory exists before calling the file absent.
"""

import pytest

from tools import file_operations
from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    home = tmp_path / "home"
    project = home / "work" / "proj"
    project.mkdir(parents=True)
    (project / "settings.yaml").write_text("needle: 1\n")
    (project / "report_2024.txt").write_text("needle\n")
    monkeypatch.setattr(file_operations, "_HOME", str(home))
    return home


def _ops(cwd):
    return ShellFileOperations(LocalEnvironment(cwd=str(cwd)))


@pytest.mark.parametrize("pattern", [
    "settings.yaml|settings.yml",
    r"report_\d+.txt",
    "^settings",
    "settings.yaml$",
])
def test_files_target_rejects_regex_tokens_before_searching(home, pattern, monkeypatch):
    ops = _ops(home)
    ran = []
    monkeypatch.setattr(ops, "_dispatch_search", lambda *a, **k: ran.append(a))

    result = ops.search(pattern, path=str(home / "work"), target="files")

    assert result.error and "glob, not a regex" in result.error
    assert ran == []


@pytest.mark.parametrize("pattern", ["*.yaml", "*settings*", "report_[0-9]*.txt", "[^a]*.txt",
                                     "*.{yaml,yml}"])
def test_files_target_accepts_standard_globs(home, pattern):
    result = _ops(home).search(pattern, path=str(home / "work"), target="files")

    assert result.error is None
    assert result.files


def test_content_search_at_home_requires_file_glob(home):
    ops = _ops(home)

    rejected = ops.search("needle", path=str(home), target="content")
    assert rejected.error and "file_glob" in rejected.error

    scoped = ops.search("needle", path=str(home), target="content", file_glob="*.yaml")
    assert scoped.error is None
    assert any(m.path.endswith("settings.yaml") for m in scoped.matches)


def test_content_search_above_home_is_rejected_even_with_glob(home):
    result = _ops(home).search("needle", path=str(home.parent), target="content", file_glob="*.yaml")

    assert result.error and "subdirectory" in result.error


def test_content_search_in_project_subdirectory_needs_no_glob(home):
    result = _ops(home).search("needle", path=str(home / "work" / "proj"), target="content")

    assert result.error is None
    assert result.total_count >= 2


def test_regex_alternation_stays_valid_for_content_search(home):
    result = _ops(home).search("needle|absent", path=str(home / "work"), target="content")

    assert result.error is None and result.total_count >= 2


def test_missing_path_reports_missing_parent(home):
    result = _ops(home).search("*.py", path=str(home / "nope" / "deeper"), target="files")

    assert "Path not found" in result.error
    assert "does not exist either" in result.error


def test_missing_path_lists_parent_beyond_first_twenty_entries(home):
    parent = home / "work" / "big"
    parent.mkdir()
    for i in range(40):
        (parent / f"aaa_{i:02d}").mkdir()
    (parent / "zzz_target_dir").mkdir()

    result = _ops(home).search("*.py", path=str(parent / "zzz_target"), target="files")

    assert "Similar paths" in result.error
    assert "zzz_target_dir" in result.error


def test_missing_path_with_existing_parent_says_no_similar_entries(home):
    result = _ops(home).search("*.py", path=str(home / "work" / "qqq"), target="files")

    assert "Parent directory" in result.error and "exists" in result.error
