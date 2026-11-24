"""Telegram polling webhook cleanup and reconnect backoff regression tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _adapter(bot) -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = bot
    return adapter


@pytest.mark.asyncio
async def test_polling_already_active_skips_delete_webhook():
    bot = SimpleNamespace(
        get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="")),
        delete_webhook=AsyncMock(),
    )

    assert await _adapter(bot)._delete_webhook_best_effort(require_success=False) is True

    bot.get_webhook_info.assert_awaited_once_with()
    bot.delete_webhook.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_webhook_is_cleared_after_probe():
    bot = SimpleNamespace(
        get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="https://example.test/telegram")),
        delete_webhook=AsyncMock(return_value=True),
    )

    assert await _adapter(bot)._delete_webhook_best_effort(require_success=False) is True

    bot.get_webhook_info.assert_awaited_once_with()
    bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)


@pytest.mark.asyncio
async def test_reconnect_probe_timeout_skips_delete_and_continues_polling():
    bot = SimpleNamespace(
        get_webhook_info=AsyncMock(side_effect=TimeoutError()),
        delete_webhook=AsyncMock(),
    )
    adapter = _adapter(bot)

    assert await adapter._delete_webhook_best_effort(require_success=False) is False

    bot.delete_webhook.assert_not_awaited()
    assert adapter._send_path_degraded is True


@pytest.mark.asyncio
async def test_reconnect_delete_timeout_remains_nonfatal():
    bot = SimpleNamespace(
        get_webhook_info=AsyncMock(return_value=SimpleNamespace(url="https://example.test/telegram")),
        delete_webhook=AsyncMock(side_effect=TimeoutError()),
    )
    adapter = _adapter(bot)

    assert await adapter._delete_webhook_best_effort(require_success=False) is False

    bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
    assert adapter._send_path_degraded is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("attempt", "expected"), ((1, 2.0), (2, 5.0), (3, 15.0), (6, 60.0)))
async def test_reconnect_backoff_schedule_and_cap(attempt, expected):
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._polling_network_error_count = attempt - 1
    adapter._stop_updater_or_go_fatal = AsyncMock(return_value=False)

    with patch("plugins.platforms.telegram.adapter.random.uniform", return_value=1.0), patch(
        "plugins.platforms.telegram.adapter.asyncio.sleep", new_callable=AsyncMock
    ) as sleep:
        await adapter._handle_polling_network_error(TimeoutError())

    sleep.assert_awaited_once_with(expected)
