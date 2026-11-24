"""Leading-dash content patterns stay positional for rg and grep."""

from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations


def test_dash_prefixed_regex_matches_with_rg_and_grep(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("HERMES_RUNTIME_DIR", str(tmp_path / "pm-runtime"))
    monkeypatch.setattr("tools.environments.local._resolve_hermes_bin_dir", lambda: None)
    (tmp_path / "bridge.py").write_text(
        "command = ['hermes', '--run-module', 'tools.demo']\nplain needle\n",
        encoding="utf-8",
    )
    env = LocalEnvironment(cwd=str(tmp_path))
    ops = ShellFileOperations(env, cwd=str(tmp_path))
    if not ops._has_command("rg"):
        import pytest
        pytest.skip("ripgrep unavailable")

    for native in ("0", "1"):
        monkeypatch.setenv("HERMES_NATIVE_FILE_READ", native)
        result = ops.search(pattern="--run-module", path=str(tmp_path))
        assert result.error is None, result.to_dict()
        assert result.total_count == 1, result.to_dict()
        assert "--run-module" in result.matches[0].content

    plain = ops.search(pattern="plain needle", path=str(tmp_path))
    assert plain.error is None and plain.total_count == 1

    monkeypatch.setattr(ops, "_has_command", lambda name: name == "grep")
    fallback = ops._search_with_grep("--run-module", str(tmp_path), None, 50, 0, "content", 0)
    assert fallback.error is None, fallback.to_dict()
    assert fallback.total_count == 1, fallback.to_dict()
