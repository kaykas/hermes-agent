"""Multiplex profiles resolve to <hermes-root>/profiles/<name>/, never ~/.openclaw/agents/<name>/."""

import json
from pathlib import Path

import pytest

from tools import profile_path_guard as guard

PROFILES = ["nova2", "cedar", "heath", "icculus", "jeter", "halo", "carlton", "hendrick", "pippin"]


@pytest.fixture
def homes(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".openclaw" / "agents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home, guard.hermes_profiles_root()


@pytest.mark.parametrize("profile", PROFILES)
def test_profile_path_maps_every_multiplex_profile_to_hermes_profiles(profile, homes):
    _, profiles_root = homes
    assert guard.profile_home(profile) == profiles_root / profile
    err = guard.check_path(str(guard.openclaw_agents_root() / profile / "sessions"))
    assert err and f"{profiles_root / profile}/sessions" in err


def test_profile_path_blocks_tilde_and_home_var_spellings(homes):
    _, profiles_root = homes
    for spelling in ("~/.openclaw/agents/nova2/sessions/", "$HOME/.openclaw/agents/nova2"):
        err = guard.check_path(spelling)
        assert err and str(profiles_root / "nova2") in err


def test_profile_path_allows_non_multiplex_agents_and_hermes_homes(homes):
    _, profiles_root = homes
    assert guard.check_path("~/.openclaw/agents/main/sessions") is None
    assert guard.check_path("~/.openclaw/agents/nova2x") is None
    assert guard.check_path(str(profiles_root / "nova2" / "sessions")) is None
    assert guard.check_command("ls ~/.hermes/profiles/nova2/sessions") is None


def test_profile_path_command_detection(homes):
    _, profiles_root = homes
    err = guard.check_command("ls -la /Users/x/.openclaw/agents/nova2/sessions/ | head")
    assert err and f"{profiles_root / 'nova2'}/sessions/" in err
    assert guard.check_command("cat ~/.openclaw/agents/mira/AGENTS.md") is None


def test_profile_path_terminal_preflight_rejects_openclaw_lookup(homes):
    from tools import terminal_tool

    with pytest.raises(terminal_tool._Rejected) as exc:
        terminal_tool._pre_exec_block(
            "ls ~/.openclaw/agents/cedar/sessions/", env=None, env_type="docker",
            cwd="/", workdir=None, session_key="test",
        )
    payload = json.loads(exc.value.result_json)
    assert payload["status"] == "blocked"
    assert "profiles/cedar" in payload["error"]


def test_profile_path_file_tools_reject_openclaw_lookup(homes):
    from tools import file_tools

    for result in (
        file_tools._handle_read_file({"path": "~/.openclaw/agents/halo/sessions/x.json"}),
        file_tools._handle_search_files({"pattern": "x", "path": "~/.openclaw/agents/halo"}),
        file_tools._handle_write_file({"path": "~/.openclaw/agents/halo/a.md", "content": "x"}),
        file_tools._handle_patch({"mode": "replace", "path": "~/.openclaw/agents/halo/a.md",
                                  "old_string": "a", "new_string": "b"}),
    ):
        assert "profiles/halo" in json.loads(result)["error"]
