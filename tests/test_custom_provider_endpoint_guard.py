"""Behaviour contract for the custom-provider ``(provider, base_url, model)`` guard.

Replays failure-replay-2026-11-02 / 2026-11-03: ``deepseek/deepseek-v4-flash`` sent to
``https://api.moonshot.ai/v1`` under ``provider: custom`` failed 3/3 attempts with HTTP 404
``resource_not_found_error``. A foreign vendor prefix on a first-party host is refused; same-vendor
namespaced ids and OpenRouter slugs pass.

Stdlib-only, so it runs under ``scripts/run_tests.sh`` and standalone (``python <this file>``).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli.runtime_provider_guard import ProviderTupleMismatch, validate_provider_tuple

MOONSHOT = "https://api.moonshot.ai/v1"


def _mismatch(provider, base_url, model):
    try:
        validate_provider_tuple(provider, base_url, model)
    except ProviderTupleMismatch as exc:
        return exc
    return None


def test_replayed_tuple_is_refused_with_an_actionable_valueerror():
    exc = _mismatch("custom", MOONSHOT, "deepseek/deepseek-v4-flash")
    assert exc is not None
    assert isinstance(exc, ValueError)  # misconfiguration: the AuthError-only fallback walker must not reroute it
    assert (exc.vendor, exc.host) == ("deepseek", "api.moonshot.ai")
    assert "deepseek/deepseek-v4-flash" in str(exc) and "openrouter.ai" in str(exc)


def test_foreign_vendor_prefix_is_refused_on_every_first_party_host_and_url_shape():
    assert _mismatch("custom:moonshot", MOONSHOT, "deepseek/deepseek-v4-flash")
    assert _mismatch("custom", "https://api.moonshot.cn/v1", "openai/gpt-5")
    assert _mismatch("CUSTOM", "https://API.Moonshot.ai/v1/", " deepseek/deepseek-v4-flash ")
    assert _mismatch("custom", "api.moonshot.ai/v1", "deepseek/deepseek-v4-flash")
    assert _mismatch("custom", "https://api.deepseek.com/v1", "moonshotai/kimi-k2")
    assert _mismatch("custom:oai", "https://api.openai.com/v1", "anthropic/claude-opus-4")


def test_same_vendor_namespaced_ids_and_openrouter_slugs_pass():
    assert not _mismatch("custom", MOONSHOT, "kimi-k3.1-pro")
    assert not _mismatch("custom", MOONSHOT, "moonshotai/kimi-k3.1-pro")
    assert not _mismatch("custom", "https://api.deepseek.com/v1", "deepseek/deepseek-v4-flash")
    assert not _mismatch("custom", "https://openrouter.ai/api/v1", "deepseek/deepseek-v4-flash")
    assert not _mismatch("custom", "https://openrouter.ai/api/v1", "moonshotai/kimi-k3.1-pro")


def test_tuples_the_guard_cannot_judge_pass():
    assert not _mismatch("custom", "http://localhost:11434/v1", "deepseek/deepseek-v4-flash")
    assert not _mismatch("custom", "http://10.0.0.5:8000/v1", "library/qwen3")
    assert not _mismatch("custom", "https://proxy.test/api.moonshot.ai/v1", "deepseek/deepseek-v4-flash")
    assert not _mismatch("custom", MOONSHOT, "my-org/finetune-7b")  # not a known vendor namespace
    assert not _mismatch("custom", MOONSHOT, None)
    assert not _mismatch("custom", None, "deepseek/deepseek-v4-flash")
    # Registered providers normalize their own model ids; only the verbatim custom path is judged.
    assert not _mismatch("kimi-coding", MOONSHOT, "deepseek/deepseek-v4-flash")
    assert not _mismatch("openrouter", "https://openrouter.ai/api/v1", "deepseek/deepseek-v4-flash")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # report every contract, not just the first failure
            failed += 1
            print(f"FAIL {name}: {exc!r}")
        else:
            print(f"PASS {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
