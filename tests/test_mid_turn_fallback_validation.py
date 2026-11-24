"""Regression tests for custom-provider fallback validation and terminal 404s."""
from types import SimpleNamespace

import pytest

from agent.error_classifier import FailoverReason, classify_api_error
from agent.route_binding import bind_route_entry
from hermes_cli.runtime_provider_guard import ProviderTupleMismatch, validate_provider_tuple

MOONSHOT = "https://api.moonshot.ai/v1"
FOREIGN_MODEL = "deepseek/deepseek-v4-flash"


def test_mid_turn_fallback_rejects_mismatch_before_client_resolution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP client resolution must not happen for a mismatched tuple")

    monkeypatch.setattr("agent.auxiliary_client.resolve_provider_client", forbidden)
    with pytest.raises(ProviderTupleMismatch):
        bind_route_entry(SimpleNamespace(), {"provider": "custom", "base_url": MOONSHOT, "model": FOREIGN_MODEL},
                         "custom", FOREIGN_MODEL)


def test_mid_turn_fallback_guard_accepts_same_vendor_tuple():
    validate_provider_tuple("custom", MOONSHOT, "kimi-k3.1-pro")


class MockAPIError(Exception):
    def __init__(self):
        super().__init__("Not found the model or Permission denied")
        self.status_code = 404
        self.body = {"error": {"code": "resource_not_found_error", "message": "Not found the model"}}
        self.response = SimpleNamespace(headers={})


def test_vendor_resource_not_found_is_terminal_on_first_attempt():
    verdict = classify_api_error(MockAPIError(), provider="custom", model=FOREIGN_MODEL)
    assert verdict.reason == FailoverReason.model_not_found
    assert verdict.retryable is False
    assert verdict.should_fallback is True
