"""Dual fleet-bot mentions permit intentional silence without a gateway error."""
from unittest.mock import AsyncMock

import pytest

from gateway.session import Platform, SessionSource
from gateway.turn_context import TurnContext
from plugins.platforms.slack.adapter import SlackAdapter
from tests.gateway.test_bot_peer_runtime_notices import _shape, _unexpected


def test_dual_fleet_mentions_are_detected_without_counting_humans():
    text = "<@U0BSDTPFXNW> <@U0BSHRLESQ4> who owns next steps?"
    assert SlackAdapter._multi_fleet_bot_mentioned(text, "U0BSDTPFXNW")
    assert SlackAdapter._multi_fleet_bot_mentioned(text, "U0BSHRLESQ4")
    # An active third bot may receive the interrupt even though only its peers were addressed.
    assert SlackAdapter._multi_fleet_bot_mentioned(text, "UTHIRDBOT")
    assert SlackAdapter._multi_fleet_bot_mentioned(
        "<@U0CEK70JRQ8> <@U0BSHRLESQ4> please coordinate", "U0BRJFJ4ZSP",
    )
    assert not SlackAdapter._multi_fleet_bot_mentioned("<@U0BSDTPFXNW> who owns next steps?", "U0BSDTPFXNW")
    assert not SlackAdapter._multi_fleet_bot_mentioned("<@U0BSDTPFXNW> <@U08PAGA081L> who owns next steps?", "U0BSDTPFXNW")


def test_multi_bot_summons_does_not_require_every_active_peer_to_reply():
    adapter = object.__new__(SlackAdapter)
    text = "<@U0BSDTPFXNW> <@U0BSHRLESQ4> please coordinate"
    for bot_uid in ("U0BSDTPFXNW", "U0BSHRLESQ4", "UTHIRDBOT"):
        assert adapter._slack_reply_expected(
            text, bot_uid, channel_id="C1", addressed=False, opens_own_session=False,
        ) is False


@pytest.mark.asyncio
async def test_dual_mentioned_human_turn_may_end_in_silence_without_error():
    source = SessionSource(
        platform=Platform.SLACK, chat_id="mpim", chat_type="mpim", user_id="human",
        multi_bot_mentioned=True,
    )
    response, silent, _ = await _shape(
        {"final_response": "[SILENT]", "api_calls": 1, "messages": []}, source,
    )
    assert silent is True and response != _unexpected()

    control = SessionSource(
        platform=Platform.SLACK, chat_id="mpim", chat_type="mpim", user_id="human",
        multi_bot_mentioned=False,
    )
    response, silent, _ = await _shape(
        {"final_response": "[SILENT]", "api_calls": 1, "messages": []}, control,
    )
    assert silent is False and response == _unexpected()


@pytest.mark.asyncio
@pytest.mark.parametrize("multi_bot_mentioned, sends_warning", [(True, False), (False, True)])
async def test_interrupted_turn_uses_the_same_multi_bot_silence_permission(
        multi_bot_mentioned, sends_warning):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._deliver_queued_first_response = AsyncMock(return_value=True)
    runner._record_room_silence_decision = AsyncMock()
    source = SessionSource(
        platform=Platform.SLACK, chat_id="mpim", chat_type="mpim", user_id="human",
        multi_bot_mentioned=multi_bot_mentioned,
    )
    turn_ctx = TurnContext(
        session_key="interrupted", stream_consumer_holder=[None], mute_notification_reply=False,
        persist_user_display_kind=None, reply_expected=True, source=source,
        _status_thread_metadata=None, event_message_id=None, inbound_message_id=None,
        run_generation=1,
    )
    result = {"final_response": "[SILENT]", "failed": False}

    await runner._run_agent_deliver_first_response(turn_ctx, None, result, result, None)

    assert runner._deliver_queued_first_response.await_count == int(sends_warning)
    if sends_warning:
        call = runner._deliver_queued_first_response.await_args
        assert call is not None
        assert "silence marker" in call.args[0]
