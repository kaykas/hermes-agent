"""``resolve_runtime_provider`` refuses an undeliverable custom (base_url, model) tuple before dispatch.

E2E against a real ``config.yaml`` in the sandboxed ``HERMES_HOME``. Replays failure-replay-2026-11-02
(``deepseek/deepseek-v4-flash`` -> ``https://api.moonshot.ai/v1`` under ``provider: custom``, HTTP 404
``resource_not_found_error`` 3/3). The configured ``model.default``, a sub-agent's ``target_model`` and
resolution-time fallback entries all resolve through this one guard.

Runs under ``scripts/run_tests.sh`` and standalone (``python <this file>`` hands itself to pytest).
"""

import os
import sys
from pathlib import Path

import pytest

if __name__ == "__main__":
    # Standalone: hand off to pytest BEFORE importing Hermes, so tests/conftest.py sandboxes
    # HERMES_HOME before any import-time path is frozen.
    _root = str(Path(__file__).resolve().parents[2])
    sys.path.insert(0, _root)
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider", "--rootdir", _root]))

from hermes_cli import runtime_provider as rp
from hermes_cli.runtime_provider_guard import ProviderTupleMismatch

MOONSHOT = "https://api.moonshot.ai/v1"


@pytest.fixture
def write_config(monkeypatch):
    for var in ("OPENAI_BASE_URL", "OPENROUTER_BASE_URL", "HERMES_INFERENCE_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    home = Path(os.environ["HERMES_HOME"])  # tests/conftest.py sandboxes this per test

    def write(text: str) -> None:
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(text, encoding="utf-8")
    return write


def _custom_moonshot(default_model: str) -> str:
    return (f"model:\n  provider: custom\n  base_url: {MOONSHOT}\n  api_key: sk-moonshot-test\n"
            f"  default: {default_model}\n")


def test_configured_default_with_foreign_vendor_prefix_is_refused(write_config):
    write_config(_custom_moonshot("deepseek/deepseek-v4-flash"))

    with pytest.raises(ProviderTupleMismatch, match="deepseek/deepseek-v4-flash"):
        rp.resolve_runtime_provider()
    with pytest.raises(ProviderTupleMismatch):
        rp.resolve_runtime_provider(requested="custom")


def test_subagent_target_model_is_judged_not_the_parent_default(write_config):
    write_config(_custom_moonshot("kimi-k3.1-pro"))

    assert rp.resolve_runtime_provider(requested="custom")["base_url"].rstrip("/") == MOONSHOT
    assert rp.resolve_runtime_provider(requested="custom", target_model="moonshotai/kimi-k3.1-pro")
    with pytest.raises(ProviderTupleMismatch):
        rp.resolve_runtime_provider(requested="custom", target_model="deepseek/deepseek-v4-flash")


def test_named_custom_provider_is_judged(write_config):
    write_config(
        "model:\n  provider: custom:moonshot\n  default: deepseek/deepseek-v4-flash\n"
        f"custom_providers:\n  - name: moonshot\n    base_url: {MOONSHOT}\n    api_key: sk-moonshot-test\n")

    with pytest.raises(ProviderTupleMismatch):
        rp.resolve_runtime_provider()


def test_same_vendor_namespaced_default_still_resolves(write_config):
    write_config(_custom_moonshot("moonshotai/kimi-k3.1-pro"))

    assert rp.resolve_runtime_provider()["provider"] == "custom"


def test_openrouter_slug_on_custom_openrouter_endpoint_still_resolves(write_config, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    write_config("model:\n  provider: custom\n  base_url: https://openrouter.ai/api/v1\n"
                 "  default: deepseek/deepseek-v4-flash\n")

    assert rp.resolve_runtime_provider()["base_url"].startswith("https://openrouter.ai")


def test_foreign_default_is_not_judged_when_resolving_another_provider(write_config, monkeypatch):
    """A caller resolving a different provider for its credentials does not send model.default there."""
    write_config(_custom_moonshot("deepseek/deepseek-v4-flash"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    assert rp.resolve_runtime_provider(requested="openrouter")["provider"] == "openrouter"


def test_mismatched_fallback_entry_is_skipped_not_dispatched(write_config):
    """Resolution-time fallback judges each entry's own tuple; a bad entry never becomes the runtime."""
    write_config("model:\n  provider: custom\n")  # bare custom, no endpoint: the primary raises AuthError
    config = {"fallback_providers": [
        {"provider": "custom", "base_url": MOONSHOT, "api_key": "sk-moonshot-test",
         "model": "deepseek/deepseek-v4-flash"},
        {"provider": "custom", "base_url": MOONSHOT, "api_key": "sk-moonshot-test", "model": "kimi-k3.1-pro"},
    ]}

    runtime, entry = rp.resolve_runtime_with_fallback(config, requested="custom")

    assert entry["model"] == "kimi-k3.1-pro"
    assert runtime["base_url"].rstrip("/") == MOONSHOT


def test_primary_mismatch_propagates_instead_of_rerouting(write_config):
    write_config(_custom_moonshot("deepseek/deepseek-v4-flash"))
    config = {"fallback_providers": [{"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"}]}

    with pytest.raises(ProviderTupleMismatch):
        rp.resolve_runtime_with_fallback(config, requested="custom")
