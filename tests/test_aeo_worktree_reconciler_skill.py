#!/usr/bin/env python3
"""Regression test for aeo-worktree-index-and-corpus-count-reconciler workflow
in agent-merge-conflict-arbiter / merge-reconciler SKILL.md."""
from pathlib import Path

import pytest

REPO_SKILL = (
    Path(__file__).resolve().parents[1]
    / "optional-skills/autonomous-ai-agents/agent-merge-conflict-arbiter/SKILL.md"
)
INSTALLED_SKILL = Path(
    "/Users/jkw/.hermes/skills/autonomous-ai-agents/merge-reconciler/SKILL.md"
)


@pytest.mark.parametrize("path", [REPO_SKILL])
def test_skill_content(path: Path) -> None:
    assert path.is_file(), f"Missing skill file: {path}"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"Missing YAML frontmatter in {path}"

    for token in (
        "public/llms.txt",
        "public/llms-full.txt",
        "generate-site-index.mjs",
        "generate-aeo-artifacts.mjs",
        "articleJsonLd.test.ts",
        "kaykas-com",
        "mira-outbound",
        "dev-tool-reviews",
    ):
        assert token in text, f"Missing required token {token!r} in {path}"

    sec_idx = text.index("## Derived-Artifact Regeneration & Corpus-Count Reconciliation")
    sec = text[sec_idx:]
    i_merge = sec.index("1. **Merge source content hunks first.**")
    i_regen = sec.index("2. **Regenerate `public/llms.txt` and `public/llms-full.txt`**")
    i_count = sec.index("3. **Recompute static JSON-LD corpus counts**")
    i_build = sec.index("4. **Run tests and production build**")
    assert i_merge < i_regen < i_count < i_build, f"Ordering invariant violated in {path}"


if __name__ == "__main__":
    for p in (REPO_SKILL, INSTALLED_SKILL):
        test_skill_content(p)
    print("PASS: test_aeo_worktree_reconciler_skill")
