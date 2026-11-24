import os

from tools import file_operations
from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations
from tools.file_operations_search import _virtual_fs_search_exclusions


def ops(home, monkeypatch):
    monkeypatch.setattr(file_operations, "_HOME", str(home))
    return ShellFileOperations(LocalEnvironment(cwd=str(home)))


def test_tilde_home_root_and_parent_are_broad(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    shell = ops(home, monkeypatch)
    for path in ("/", "~", str(home), str(home.parent)):
        assert shell._is_broad_local_search_root(path), path


def test_hermes_and_claude_project_stores_are_broad(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = home / "work" / "project"
    project.mkdir(parents=True)
    shell = ops(home, monkeypatch)
    assert shell._is_broad_local_search_root(str(home / ".hermes"))
    assert shell._is_broad_local_search_root(str(home / ".claude" / "projects"))
    assert not shell._is_broad_local_search_root(str(home / ".hermes" / "scripts"))
    assert not shell._is_broad_local_search_root(str(project))


def test_file_search_over_broad_roots_is_rejected(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = home / "work" / "project"
    project.mkdir(parents=True)
    shell = ops(home, monkeypatch)
    for path in ("/", "~", str(home), str(home / ".hermes"), str(home / ".claude" / "projects")):
        monkeypatch.setenv("HOME", str(home))
        result = shell._preflight_validate_search("*.py", path, "files", None)
        assert result and result.error and "unbounded" in result.error
    assert shell._preflight_validate_search("*.py", str(project), "files", None) is None


def test_virtual_filesystems_and_caches_are_relative_exclusions(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert _virtual_fs_search_exclusions("/", home=str(home), platform="darwin") == [
        "dev/fd", "private/var/agentx", os.path.relpath(home / "Library" / "Caches", "/"),
    ]
    assert _virtual_fs_search_exclusions(str(home), home=str(home), platform="darwin") == [
        "Library/Caches",
    ]
    assert _virtual_fs_search_exclusions("/dev/fd", home=str(home), platform="darwin") == []
