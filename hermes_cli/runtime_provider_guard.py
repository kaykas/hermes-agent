"""Pre-dispatch guard for the resolved ``(provider, base_url, model)`` tuple of a custom endpoint.

``provider: custom`` sends the model id to its endpoint verbatim (``model_normalize`` leaves custom
alone). A ``<vendor>/<model>`` slug is OpenRouter's namespace: sent to a different vendor's own API
(``deepseek/deepseek-v4-flash`` to ``https://api.moonshot.ai/v1``), the host answers HTTP 404
``resource_not_found_error`` on every attempt (failure-replay 2026-11-02 / 2026-11-03).
``resolve_runtime_provider`` runs this guard after its ladder, so the main turn, delegated
sub-agents (``target_model``), the configured ``model.default`` and resolution-time fallback
entries are all judged before any request is dispatched.

The guard is deliberately narrow. It refuses a tuple only when the provider is custom, the host is
a known first-party vendor API, AND the slug's prefix is a known vendor namespace that host does
not own. Same-vendor namespaced ids (``moonshotai/kimi-k2`` on Moonshot), aggregators
(OpenRouter), proxies, LAN servers and unknown hosts all pass. Registered providers own their
model-id normalization (``deepseek`` strips any vendor prefix), so they are not judged here.

Stdlib-only: it runs on every resolution and is importable without the runtime's dependencies.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

# First-party API host -> the slug namespaces that host itself answers to.
_FIRST_PARTY_HOSTS: dict[str, frozenset[str]] = {
    "api.moonshot.ai": frozenset({"moonshotai", "moonshot", "kimi"}),
    "api.moonshot.cn": frozenset({"moonshotai", "moonshot", "kimi"}),
    "api.deepseek.com": frozenset({"deepseek", "deepseek-ai"}),
    "api.openai.com": frozenset({"openai"}),
    "api.anthropic.com": frozenset({"anthropic"}),
    "generativelanguage.googleapis.com": frozenset({"google", "models"}),
    "api.x.ai": frozenset({"x-ai", "xai"}),
    "api.mistral.ai": frozenset({"mistralai", "mistral"}),
    "api.minimax.io": frozenset({"minimax"}),
    "api.z.ai": frozenset({"z-ai", "zai", "zhipu"}),
}

# Vendor namespaces as they appear in OpenRouter-style slugs. Only these prefixes count as a
# foreign vendor; anything else (an org path, a fine-tune namespace) is not judged.
_VENDOR_NAMESPACES = frozenset({
    "openai", "anthropic", "google", "deepseek", "deepseek-ai", "moonshotai", "moonshot", "kimi",
    "x-ai", "xai", "mistralai", "mistral", "meta-llama", "qwen", "minimax", "z-ai", "zai", "zhipu",
    "cohere", "nvidia", "microsoft", "amazon", "perplexity", "nousresearch",
})


class ProviderTupleMismatch(ValueError):
    """The model slug names a different vendor than the first-party host it would be sent to.

    A ``ValueError``, not an ``AuthError``: this is misconfiguration, so the resolution-time
    fallback walker (which only advances on ``AuthError``) does not silently reroute it."""

    def __init__(self, provider: str, base_url: str, model: str, vendor: str, host: str) -> None:
        self.provider, self.base_url, self.model, self.vendor, self.host = provider, base_url, model, vendor, host
        super().__init__(
            f"Model '{model}' is an OpenRouter-style '{vendor}/' slug, but provider '{provider}' sends it "
            f"verbatim to {host} ({base_url}), which does not serve '{vendor}' models and would answer "
            f"HTTP 404 resource_not_found_error. Use a model id {host} serves, or point base_url at an "
            "endpoint that serves vendor-namespaced slugs such as https://openrouter.ai/api/v1.")


def _hostname(base_url: str) -> str:
    # Same parse as ``utils.base_url_hostname`` (scheme-less ``host/path`` tolerated, exact host only).
    try:
        return (urlparse(base_url if "://" in base_url else f"//{base_url}").hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _is_custom_provider(provider: str) -> bool:
    return provider == "custom" or provider.startswith("custom:")


def provider_tuple_mismatch(provider: Optional[str], base_url: Optional[str],
                            model: Optional[str]) -> Optional[ProviderTupleMismatch]:
    """The mismatch for an undeliverable tuple, or ``None`` when the guard cannot fault it."""
    name = str(provider or "").strip().lower()
    url = str(base_url or "").strip()
    slug = str(model or "").strip()
    if not _is_custom_provider(name) or not url or "/" not in slug:
        return None
    host = _hostname(url)
    owned = _FIRST_PARTY_HOSTS.get(host)
    vendor = slug.split("/", 1)[0].strip().lower()
    if owned is None or vendor not in _VENDOR_NAMESPACES or vendor in owned:
        return None
    return ProviderTupleMismatch(name, url, slug, vendor, host)


def validate_provider_tuple(provider: Optional[str], base_url: Optional[str], model: Optional[str]) -> None:
    """Raise :class:`ProviderTupleMismatch` when ``model`` names a foreign vendor for ``base_url``."""
    error = provider_tuple_mismatch(provider, base_url, model)
    if error is not None:
        raise error
