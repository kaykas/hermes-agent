# Post-delivery cron insight attribution

## Problem

Nova2 job `9904e321beb0` writes `insight-drafts/sent-ids.json` during its LLM turn. That is send **intent**, not delivery evidence: Hermes delivers the final response afterward. The separate `owner-loop-sender.py` transport excludes Nova2, so it cannot close the attribution gap.

## Design

Attribution is opt-in through an in-memory/persisted job field named `insight_attribution`. No job has this field by default, so the new scheduler path is a no-op until deliberately enabled.

The hook runs at the two successful target-delivery seams in `cron/scheduler_delivery.py`:

1. `_deliver_via_live_adapter`, after the adapter confirms success; and
2. `_deliver_standalone`, after the fallback sender succeeds.

A platform `message_id` is mandatory. A bare/evidence-free success, timeout assumed delivered, failed send, suppressed send, and failure notice do not attribute the insight. Queued worker results pass through the same seams when the gateway drains them.

After confirmation, `cron/insight_attribution.py`:

1. Reads the execution's `started_at` (or `claimed_at`) and selects only rich `sent-ids.json` `entries` rows written in that execution's starting minute or later. Minute truncation is required because existing Nova2 `sent_at` values have minute precision.
2. Includes the row's `id` and optional `companion_id`. Legacy date arrays and the aggregate `sent_ids` array have no execution timestamp and are never promoted into delivery evidence.
3. Appends an idempotent `cron-insight-delivery-v1` receipt containing the execution/job/insight IDs, platform/chat/thread, transport, platform message ID, delivered content SHA-256, and delivery timestamp.
4. Projects the receipt onto matching `drafts-*.json` rows as `delivered: true`, `delivered_at`, and `delivery_receipt_ids`. The receipt is authoritative and is written before this best-effort projection, so replay repairs a missing projection without duplicating the receipt.

`sent-ids.json` is strictly read-only and is never normalized or rewritten. Draft writes use atomic replacement. Attribution exceptions are contained and logged; they cannot convert an already-sent message into a delivery failure or trigger a resend.

## Sandbox/activation boundary

Tests inject temporary paths and an in-memory job field. They use mocked live and standalone transports and do not read or write production insight state.

A future production activation must explicitly add this field to job `9904e321beb0` (and use the active Nova2 profile defaults):

```json
"insight_attribution": {"enabled": true}
```

That activation is intentionally **not** included here. It also requires the updated scheduler code to be loaded by the Nova2 gateway. No cron config, production draft, production `sent-ids.json`, or delivery channel is changed by this implementation.

## Required production receipt

Before treating the feature as live-verified, one real Nova2 result delivery must produce all of the following:

- the scheduler's confirmed Telegram send log with a non-empty `message_id` and the run's `execution_id`;
- one `~/.hermes/profiles/nova2/insight-drafts/delivery-receipts.jsonl` row joining that execution and message ID to only the current run's insight/companion IDs;
- matching draft rows marked delivered with that receipt ID, while `sent-ids.json` remains byte-for-byte unchanged by the hook;
- the corresponding Telegram message observed in chat.

A separate real or staging forced-failure run should confirm that no receipt or draft projection appears. Mocked tests establish code behavior, not Telegram receipt or production activation.
