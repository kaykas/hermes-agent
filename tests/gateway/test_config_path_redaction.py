"""Chat-bound config path normalization and attachment denial."""

import asyncio
import contextlib
from types import SimpleNamespace
from typing import cast

from gateway.config import Platform
from gateway.config_path_guard import is_protected_config_attachment, redact_config_paths
from gateway.platforms.base import BasePlatformAdapter, MessageEvent
from gateway.run import _redact_config_paths, _sanitize_gateway_final_response


def test_redact_config_paths_rewrites_all_supported_paths():
    raw = (
        "see /Users/jkw/.hermes/config.yaml, /Users/other/.openclaw/config.yml, "
        "/Users/jkw/.openclaw/openclaw.json, and /home/jkw/.claude/.mcp.json"
    )
    assert redact_config_paths(raw) == (
        "see ~/.hermes/config.yaml, ~/.openclaw/config.yml, "
        "~/.openclaw/openclaw.json, and ~/.claude/.mcp.json"
    )
    assert _redact_config_paths is redact_config_paths


def test_final_chat_response_uses_tilde_config_paths():
    raw = "Edit /Users/jkw/.hermes/config.yaml, then check /Users/jkw/.openclaw/openclaw.json."
    assert _sanitize_gateway_final_response(Platform.TELEGRAM, raw) == (
        "Edit ~/.hermes/config.yaml, then check ~/.openclaw/openclaw.json."
    )


def test_unrelated_paths_and_raw_local_surface_stay_unchanged():
    raw = "/Users/jkw/.hermes/scripts/example.py"
    assert redact_config_paths(raw) == raw
    assert _sanitize_gateway_final_response("local", raw) == raw
    assert not is_protected_config_attachment(raw)


def test_protected_attachment_filters_reject_absolute_and_tilde_paths():
    protected = [
        "/Users/jkw/.hermes/config.yaml",
        "~/.openclaw/config.yaml",
        "/home/other/.openclaw/openclaw.json",
        "~/.claude/.mcp.json",
    ]
    assert all(is_protected_config_attachment(path) for path in protected)
    assert BasePlatformAdapter.filter_media_delivery_paths([(path, False) for path in protected]) == []
    assert BasePlatformAdapter.filter_local_delivery_paths(protected) == []


def test_extraction_redacts_before_candidates_and_drops_protected_attachment():
    class Dummy:
        _extract_response_content = BasePlatformAdapter._extract_response_content
        extract_media = staticmethod(BasePlatformAdapter.extract_media)
        extract_images = staticmethod(BasePlatformAdapter.extract_images)
        extract_local_files = staticmethod(lambda text: (
            (["~/.hermes/config.yaml"], "Check")
            if "~/.hermes/config.yaml" in text else ([], text)
        ))
        filter_media_delivery_paths = staticmethod(BasePlatformAdapter.filter_media_delivery_paths)
        filter_local_delivery_paths = staticmethod(BasePlatformAdapter.filter_local_delivery_paths)

        @contextlib.contextmanager
        def _media_delivery_scope(self, source):
            yield

        async def _bounded_history_media_paths_for_session(self, session_key):
            return None

    event = cast(MessageEvent, SimpleNamespace(source=SimpleNamespace(chat_id="chat")))
    extracted = asyncio.run(Dummy()._extract_response_content(
        "Check /Users/jkw/.hermes/config.yaml", event, "session", is_ephemeral_response=False))
    assert extracted.media_files == []
    assert extracted.local_files == []
    assert "/Users/jkw/" not in extracted.text_content
    assert extracted.text_content == "Check"


def test_direct_send_document_refuses_protected_config_without_transport():
    class Dummy:
        name = "dummy"
        send_document = BasePlatformAdapter.send_document

        async def _send_media_fallback_notice(self, *args, **kwargs):
            raise AssertionError("protected config reached the transport fallback")

    result = asyncio.run(Dummy().send_document("chat", "~/.hermes/config.yaml"))
    assert not result.success
    assert result.error == "protected configuration attachment refused"
    assert result.raw_response == {"failure_notice_sent": True}
