"""Missing skill lookups return nearest names and fail closed on exact repeats."""

import json

import pytest

from tools.skills_tool import _skill_view_with_bump, reset_skill_view_dedup


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    skill = home / "skills" / "fleet-credential-verification"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fleet-credential-verification\ndescription: Verify fleet credentials.\n---\n"
        "# Credential verification\n\nRun the read-only verification procedure.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    reset_skill_view_dedup()
    yield home
    reset_skill_view_dedup()


def view(name, task="turn-missing-skill"):
    return json.loads(_skill_view_with_bump({"name": name}, task_id=task))


def test_first_miss_suggests_and_repeat_is_blocked(skills_home):
    first = view("fleet-credential-rollout")
    assert first["success"] is False
    assert "fleet-credential-verification" in first["closest_matches"]
    assert first.get("blocked") is not True

    second = view("fleet-credential-rollout")
    assert second["success"] is False
    assert second["blocked"] is True
    assert second["duplicate_missing"] is True
    assert "fleet-credential-verification" in second["closest_matches"]


def test_inventory_is_cached_across_different_misses(skills_home, monkeypatch):
    import tools.skills_tool as st

    calls = 0
    real = st.find_all_skills

    def counted():
        nonlocal calls
        calls += 1
        return real()

    monkeypatch.setattr(st, "find_all_skills", counted)
    view("fleet-credential-rollout")
    view("fleet-credential-rollout-two")
    assert calls == 1


def test_valid_skill_still_loads_and_reset_allows_reevaluation(skills_home):
    view("fleet-credential-rollout")
    valid = view("fleet-credential-verification")
    assert valid["success"] is True
    assert "read-only verification" in valid["content"]

    reset_skill_view_dedup("turn-missing-skill")
    reevaluated = view("fleet-credential-rollout")
    assert reevaluated["success"] is False
    assert reevaluated.get("blocked") is not True
