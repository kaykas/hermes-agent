"""Nova2-style post-delivery insight attribution, with mocked transports only."""

import asyncio
import datetime as dt
import json
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cron import insight_attribution
from cron.scheduler import _deliver_result
from gateway.config import Platform, PlatformConfig


CHAT_ID = "8456174966"
NOW = dt.datetime.now(dt.UTC)
CURRENT_SENT_AT = (NOW - dt.timedelta(seconds=20)).isoformat().replace("+00:00", "Z")
OLD_SENT_AT = "2026-10-21T21:06:00Z"


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def insight_state(tmp_path):
    """Mixed production-shaped sent-ids schema, normalized only in this fixture."""
    root = tmp_path / "insight-drafts"
    sent_ids = root / "sent-ids.json"
    _write_json(sent_ids, {
        "2026-10-21": ["insight-old"],
        "2026-10-30": ["insight-current", "insight-companion"],
        "sent_ids": ["insight-old", "insight-current", "insight-companion"],
        "entries": [
            {"id": "insight-old", "sent_at": OLD_SENT_AT, "date": "2026-10-21"},
            {
                "id": "insight-current", "companion_id": "insight-companion",
                "sent_at": CURRENT_SENT_AT, "date": "2026-10-30",
            },
        ],
    })
    drafts = root / "drafts-2026-10-30.json"
    _write_json(drafts, [
        {"id": "insight-old", "score": 0.8},
        {"id": "insight-current", "score": 0.9},
        {"id": "insight-companion", "score": 0.7},
    ])
    return {
        "root": root,
        "sent_ids": sent_ids,
        "drafts": drafts,
        "receipts": root / "delivery-receipts.jsonl",
    }


def _job(insight_state, *, enabled=True):
    return {
        "id": "9904e321beb0",
        "execution_id": "fixture-execution",
        "name": "owner-loop-proactive-send",
        "deliver": "origin",
        "origin": {"platform": "telegram", "chat_id": CHAT_ID},
        "insight_attribution": {
            "enabled": enabled,
            "sent_ids_path": str(insight_state["sent_ids"]),
            "drafts_dir": str(insight_state["root"]),
            "receipts_path": str(insight_state["receipts"]),
        },
    }


def _gateway_config():
    config = MagicMock()
    config.platforms = {Platform.TELEGRAM: PlatformConfig(enabled=True)}
    config.get_home_channel = lambda _platform: None
    return config


def _drive(job, *, live_result=None, live_error=None, standalone_result=None, for_failure=False):
    """Drive the real scheduler seam; no network adapter or sender is used."""
    loop = MagicMock()
    loop.is_running.return_value = True
    router_calls = []
    standalone_calls = []

    def fake_run_coro(coro, _loop):
        future = Future()
        try:
            future.set_result(asyncio.run(coro))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    router = MagicMock()

    async def deliver(target, text, metadata, transport=None):
        router_calls.append({"target": target, "text": text, "metadata": metadata})
        if live_error is not None:
            raise live_error
        return live_result

    router._deliver_to_platform = deliver

    async def standalone(_platform, _pconfig, chat_id, text, **kwargs):
        standalone_calls.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
        return standalone_result if standalone_result is not None else {"error": "forced standalone failure"}

    adapters = {Platform.TELEGRAM: MagicMock()}
    with patch("gateway.config.load_gateway_config", return_value=_gateway_config()), \
         patch("cron.scheduler.load_config", return_value={"cron": {"wrap_response": False}}), \
         patch("cron.scheduler_delivery._seed_live_delivery_sessions"), \
         patch("cron.scheduler_delivery._record_delivery_verification"), \
         patch("gateway.delivery.DeliveryRouter", return_value=router), \
         patch("gateway.outbound.send._send_to_platform", standalone), \
         patch("asyncio.run_coroutine_threadsafe", side_effect=fake_run_coro), \
         patch.object(insight_attribution, "_execution_started_at", return_value=NOW - dt.timedelta(minutes=1)):
        error = _deliver_result(
            job, "Mocked insight payload", adapters=adapters, loop=loop, for_failure=for_failure
        )
    return error, router_calls, standalone_calls


def _receipt_rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_forced_transport_failure_leaves_intent_unattributed(insight_state):
    before = insight_state["sent_ids"].read_bytes()
    error, router_calls, standalone_calls = _drive(
        _job(insight_state),
        live_result=SimpleNamespace(success=False, message_id=None, raw_response=None, error="forced live failure"),
        standalone_result={"error": "forced standalone failure"},
    )

    assert error is not None and "forced standalone failure" in error
    assert len(router_calls) == 1 and len(standalone_calls) == 1
    assert _receipt_rows(insight_state["receipts"]) == []
    assert all("delivered" not in draft for draft in _read_json(insight_state["drafts"]))
    assert insight_state["sent_ids"].read_bytes() == before


def test_confirmed_live_delivery_writes_receipt_and_projects_drafts(insight_state):
    before = insight_state["sent_ids"].read_bytes()
    error, router_calls, standalone_calls = _drive(
        _job(insight_state),
        live_result=SimpleNamespace(success=True, message_id=731, raw_response={"ok": True}),
    )

    assert error is None and len(router_calls) == 1 and standalone_calls == []
    rows = _receipt_rows(insight_state["receipts"])
    assert len(rows) == 1
    assert rows[0]["job_id"] == "9904e321beb0"
    assert rows[0]["execution_id"] == "fixture-execution"
    assert rows[0]["insight_ids"] == ["insight-current", "insight-companion"]
    assert rows[0]["platform"] == "telegram" and rows[0]["chat_id"] == CHAT_ID
    assert rows[0]["message_id"] == "731" and rows[0]["transport"] == "live_adapter"
    drafts = {draft["id"]: draft for draft in _read_json(insight_state["drafts"])}
    assert drafts["insight-current"]["delivered"] is True
    assert drafts["insight-companion"]["delivered"] is True
    assert "delivered" not in drafts["insight-old"]
    assert insight_state["sent_ids"].read_bytes() == before


def test_standalone_success_uses_same_attribution_seam(insight_state):
    error, _, standalone_calls = _drive(
        _job(insight_state),
        live_result=SimpleNamespace(success=False, message_id=None, raw_response=None, error="force fallback"),
        standalone_result={"message_id": "standalone-9"},
    )

    assert error is None and len(standalone_calls) == 1
    rows = _receipt_rows(insight_state["receipts"])
    assert len(rows) == 1
    assert rows[0]["message_id"] == "standalone-9" and rows[0]["transport"] == "standalone"


def test_success_without_platform_message_id_stays_pending(insight_state):
    error, _, _ = _drive(
        _job(insight_state),
        live_result=SimpleNamespace(success=True, message_id=None, raw_response={"ok": True}),
    )

    assert error is None
    assert _receipt_rows(insight_state["receipts"]) == []
    assert all("delivered" not in draft for draft in _read_json(insight_state["drafts"]))


def test_replay_is_idempotent_and_does_not_rewrite_sent_ids(insight_state):
    job = _job(insight_state)
    before = insight_state["sent_ids"].read_bytes()
    result = SimpleNamespace(success=True, message_id=731, raw_response={"ok": True})

    assert _drive(job, live_result=result)[0] is None
    first_receipt = insight_state["receipts"].read_bytes()
    assert _drive(job, live_result=result)[0] is None

    assert insight_state["receipts"].read_bytes() == first_receipt
    assert len(_receipt_rows(insight_state["receipts"])) == 1
    for draft in _read_json(insight_state["drafts"]):
        if draft["id"] != "insight-old":
            assert len(draft["delivery_receipt_ids"]) == 1
    assert insight_state["sent_ids"].read_bytes() == before


def test_disabled_job_has_no_attribution_side_effects(insight_state):
    error, _, _ = _drive(
        _job(insight_state, enabled=False),
        live_result=SimpleNamespace(success=True, message_id=731, raw_response={"ok": True}),
    )

    assert error is None
    assert not insight_state["receipts"].exists()
    assert all("delivered" not in draft for draft in _read_json(insight_state["drafts"]))


def test_failure_notice_is_not_an_insight_delivery(insight_state):
    error, _, _ = _drive(
        _job(insight_state), for_failure=True,
        live_result=SimpleNamespace(success=True, message_id=732, raw_response={"ok": True}),
    )

    assert error is None
    assert _receipt_rows(insight_state["receipts"]) == []
    assert all("delivered" not in draft for draft in _read_json(insight_state["drafts"]))


def test_attribution_error_cannot_turn_confirmed_send_into_failure(insight_state):
    with patch.object(insight_attribution, "_candidate_ids", side_effect=OSError("fixture disk failure")):
        error, router_calls, standalone_calls = _drive(
            _job(insight_state),
            live_result=SimpleNamespace(success=True, message_id=733, raw_response={"ok": True}),
        )

    assert error is None and len(router_calls) == 1 and standalone_calls == []
    assert _receipt_rows(insight_state["receipts"]) == []


def test_minute_precision_intent_in_execution_minute_is_eligible(insight_state):
    started = dt.datetime(2026, 10, 30, 21, 26, 45, tzinfo=dt.UTC)
    delivered = started + dt.timedelta(seconds=10)
    sent_ids = insight_state["sent_ids"]
    payload = _read_json(sent_ids)
    payload["entries"] = [
        {"id": "insight-old", "sent_at": "2026-10-30T21:25:59Z"},
        {"id": "insight-current", "sent_at": "2026-10-30T21:26:00Z"},
    ]
    _write_json(sent_ids, payload)

    ids = insight_attribution._candidate_ids(sent_ids, started, delivered)

    assert ids == ["insight-current"]
