"""Auxiliary tasks leave a mismatched main tuple before client construction."""
from agent import auxiliary_client

MOONSHOT = "https://api.moonshot.ai/v1"
FOREIGN_MODEL = "deepseek/deepseek-v4-flash"


def test_mismatched_main_default_falls_through_without_client_resolution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("mismatched model.default reached client resolution")

    monkeypatch.setattr(auxiliary_client, "resolve_provider_client", forbidden)
    result = auxiliary_client._try_main_provider_route(
        "custom", FOREIGN_MODEL, MOONSHOT, "test-key", "chat_completions")
    assert result is None


def test_same_vendor_main_default_keeps_custom_endpoint(monkeypatch):
    marker = object()
    calls = []

    def resolve(provider, model, **kwargs):
        calls.append((provider, model, kwargs))
        return marker, "kimi-k3.1-pro"

    monkeypatch.setattr(auxiliary_client, "resolve_provider_client", resolve)
    monkeypatch.setattr(auxiliary_client, "_is_provider_unhealthy", lambda *args, **kwargs: False)
    result = auxiliary_client._try_main_provider_route(
        "custom", "kimi-k3.1-pro", MOONSHOT, "test-key", "chat_completions")
    assert result == (marker, "kimi-k3.1-pro", "custom")
    assert calls[0][2]["explicit_base_url"] == MOONSHOT
