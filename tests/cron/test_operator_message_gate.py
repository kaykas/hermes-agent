from cron import scheduler_delivery as delivery


def test_unsupported_statistic_is_held_before_any_transport(monkeypatch):
    monkeypatch.setattr(delivery, "_resolve_delivery_targets", lambda job, for_failure=False, skipped=None: [
        {"platform": "telegram", "chat_id": "synthetic"}
    ])
    recorded = []
    monkeypatch.setattr(delivery, "_record_delivery_verification", lambda job, rows: recorded.append(rows))
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "execution-1")
    job = {"id": "synthetic-job", "execution_id": "execution-1", "deliver": "telegram",
           "operator_message_quality": "hold"}
    content = "Over 70% of property sales turnover comes from administrative overload."

    error = delivery._deliver_result(job, content)

    assert error == "operator-message-quality hold: unsupported_statistic"
    assert job["last_delivery_error"] == error
    assert recorded == [[]]


def test_no_agent_script_stdout_supports_its_own_percentages(monkeypatch):
    content = "Win week 16% · Top 3 42% · preliminary script readout"
    assert delivery._operator_message_quality_hold(content, evidence=content) is None

    monkeypatch.setattr(delivery, "_resolve_delivery_targets", lambda job, for_failure=False, skipped=None: [
        {"platform": "telegram", "chat_id": "synthetic"}
    ])
    seen = {}

    def stop_after_quality_check(content, evidence="", schedule_evidence=""):
        seen["evidence"] = evidence
        return []

    monkeypatch.setattr(delivery, "_lint_operator_message", stop_after_quality_check)
    monkeypatch.setattr("cron.delivery_queue.enqueue_and_wait", lambda *args, **kwargs: None)
    monkeypatch.setattr("cron.delivery_queue.get_status", lambda *args, **kwargs: None)
    monkeypatch.setattr("cron.jobs.get_job", lambda *args, **kwargs: {})
    monkeypatch.setattr(delivery, "_record_delivery_verification", lambda job, rows: None)
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "execution-1")
    content = "Win week 16% · Top 3 42% · preliminary script readout"
    job = {
        "id": "synthetic-script-job", "execution_id": "execution-1",
        "deliver": "telegram", "no_agent": True,
    }

    assert delivery._deliver_result(job, content) is None
    assert seen["evidence"] == content


def test_agent_output_does_not_inherit_script_evidence(monkeypatch):
    monkeypatch.setattr(delivery, "_resolve_delivery_targets", lambda job, for_failure=False, skipped=None: [
        {"platform": "telegram", "chat_id": "synthetic"}
    ])
    monkeypatch.setattr(delivery, "_record_delivery_verification", lambda job, rows: None)
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "execution-1")
    job = {"id": "synthetic-agent-job", "execution_id": "execution-1", "deliver": "telegram",
           "operator_message_quality": "hold"}

    assert delivery._deliver_result(job, "Win week 16%") == (
        "operator-message-quality hold: unsupported_statistic"
    )


def _stub_transport(monkeypatch, sent):
    monkeypatch.setattr(delivery, "_resolve_delivery_targets", lambda job, for_failure=False, skipped=None: [
        {"platform": "telegram", "chat_id": "synthetic"}
    ])
    monkeypatch.setattr("cron.delivery_queue.enqueue_and_wait",
                        lambda *args, **kwargs: sent.append((args, kwargs)))
    monkeypatch.setattr("cron.delivery_queue.get_status", lambda *args, **kwargs: None)
    monkeypatch.setattr("cron.jobs.get_job", lambda *args, **kwargs: {})
    monkeypatch.setattr(delivery, "_record_delivery_verification", lambda job, rows: None)
    monkeypatch.setenv("_HERMES_CRON_EXTERNAL_WORKER", "execution-1")


def test_warn_mode_is_default_and_still_delivers_flagged_prose(monkeypatch):
    sent = []
    _stub_transport(monkeypatch, sent)
    monkeypatch.setattr(delivery._sched, "load_config", lambda: {"cron": {}})
    job = {"id": "synthetic-agent-job", "execution_id": "execution-1", "deliver": "telegram"}

    assert delivery._deliver_result(job, "Win week 16%") is None
    assert sent, "flagged message must still reach transport in warn mode"
    assert job["last_quality_findings"] == "operator-message-quality hold: unsupported_statistic"
    assert "last_delivery_error" not in job


def test_config_hold_mode_still_blocks(monkeypatch):
    sent = []
    _stub_transport(monkeypatch, sent)
    monkeypatch.setattr(delivery._sched, "load_config",
                        lambda: {"cron": {"operator_message_quality": "hold"}})
    job = {"id": "synthetic-agent-job", "execution_id": "execution-1", "deliver": "telegram"}

    assert delivery._deliver_result(job, "Win week 16%") == (
        "operator-message-quality hold: unsupported_statistic"
    )
    assert not sent


def test_heading_and_production_overclaim_hold_codes():
    heading = delivery._operator_message_quality_hold("2. **Single-Source Build Pipeline Verified**")
    production = delivery._operator_message_quality_hold(
        "All three proactive safeguards are now implemented, deployed, and verified."
    )
    assert heading == "operator-message-quality hold: heading_style"
    assert production == "operator-message-quality hold: production_overclaim"


def test_clean_message_has_no_quality_hold():
    assert delivery._operator_message_quality_hold(
        "### 2. Commercial story\nSeven calendars are visible. The read-only check succeeded."
    ) is None


def test_schedule_scope_uses_the_jobs_verified_cron_evidence():
    content = "Daily Digest and forecast ticker now both land at 9:30 a.m. Pacific, Monday–Friday."
    job = {
        "name": "tom daily forecast ticker",
        "schedule": {"kind": "cron", "expr": "30 9 * * 2-5"},
        "schedule_display": "30 9 * * 2-5",
    }
    assert delivery._operator_message_quality_hold(content, job=job) == (
        "operator-message-quality hold: schedule_scope"
    )


def test_opening_frame_is_held_without_external_evidence():
    assert delivery._operator_message_quality_hold(
        "Here is the plain-English breakdown for #4."
    ) == "operator-message-quality hold: opening_frame"


def test_correct_ticker_explanation_has_no_schedule_hold():
    job = {"name": "tom daily forecast ticker", "schedule": {"expr": "30 9 * * 2-5"}}
    assert delivery._operator_message_quality_hold(
        "The forecast ticker runs Tuesday–Friday; Daily Digest runs Monday–Friday.",
        job=job,
    ) is None


def test_job_name_is_not_general_evidence():
    production_job = {"name": "Observed in production: safeguards succeeded"}
    assert delivery._operator_message_quality_hold(
        "Safeguards are implemented, deployed, and verified.", job=production_job,
    ) == "operator-message-quality hold: production_overclaim"
    count_job = {"name": "4 meetings found"}
    assert delivery._operator_message_quality_hold(
        "Three meetings were scored.", job=count_job,
    ) is None
