"""CDPSupervisor reconnect budget: a supervisor that attached once must not retry a dead
endpoint forever (#114172). No real Chrome — ``websockets.connect`` is stubbed."""

from __future__ import annotations

import asyncio

import pytest
import websockets

from tools import browser_supervisor as bs


class _ClosingWebSocket:
    async def close(self):
        pass


@pytest.fixture
def unregister():
    task_ids: list[str] = []
    yield task_ids
    for task_id in task_ids:
        bs.SUPERVISOR_REGISTRY._pop(task_id)


def test_post_attach_reconnects_stop_at_budget_and_unregister(monkeypatch, unregister, caplog):
    """After the first attach, a dead endpoint gets MAX_POST_ATTACH_RECONNECT_FAILURES dials,
    one final warning, and the supervisor leaves the registry — never an unbounded loop."""
    supervisor = bs.CDPSupervisor(task_id="bounded-reconnect", cdp_url="ws://browserbase.example")
    bs.SUPERVISOR_REGISTRY._by_task[supervisor.task_id] = supervisor
    unregister.append(supervisor.task_id)
    budget = bs.MAX_POST_ATTACH_RECONNECT_FAILURES
    dials = 0

    async def connect(*_args, **_kwargs):
        nonlocal dials
        dials += 1
        if dials == 1:
            return _ClosingWebSocket()
        if dials <= budget + 1:
            raise ConnectionError("CDP endpoint is gone")
        await asyncio.Event().wait()  # would hang forever: the loop must never get here

    async def _noop(*_a, **_k):
        pass

    real_sleep = asyncio.sleep

    async def fast_sleep(_delay):
        await real_sleep(0)  # yield so wait_for's deadline can fire on an unbounded loop

    monkeypatch.setattr(websockets, "connect", connect)
    monkeypatch.setattr(supervisor, "_attach_initial_page", _noop)
    monkeypatch.setattr(supervisor, "_read_loop", _noop)
    monkeypatch.setattr(bs.asyncio, "sleep", fast_sleep)

    with caplog.at_level("WARNING", logger="tools.browser_supervisor"):
        asyncio.run(asyncio.wait_for(supervisor._run(), timeout=2.0))

    assert dials == budget + 1  # attach + budgeted reconnects
    final = [r.getMessage() for r in caplog.records if "stopped after" in r.getMessage()]
    assert len(final) == 1 and f"{budget} failed reconnect" in final[0]
    assert supervisor.snapshot().active is False
    assert bs.SUPERVISOR_REGISTRY.get(supervisor.task_id) is None


def test_initial_connect_failure_stays_fatal_for_start(monkeypatch):
    """The budget is post-attach only: a first-dial failure still propagates to ``start()``."""
    supervisor = bs.CDPSupervisor(task_id="initial-failure", cdp_url="ws://127.0.0.1:9222")

    async def connect(*_args, **_kwargs):
        raise ConnectionError("CDP endpoint is unavailable")

    monkeypatch.setattr(websockets, "connect", connect)

    asyncio.run(supervisor._run())

    assert isinstance(supervisor._start_error, ConnectionError)
    assert supervisor._ready_event.is_set()
    assert supervisor.snapshot().active is False


def test_dead_local_browser_stops_without_reconnect(monkeypatch, unregister):
    """A confirmed dead local Chrome owner gets zero reconnect dials after socket loss."""
    import psutil
    supervisor = bs.CDPSupervisor(task_id="dead-local-browser", cdp_url="ws://127.0.0.1:9222")
    bs.SUPERVISOR_REGISTRY._by_task[supervisor.task_id] = supervisor
    unregister.append(supervisor.task_id)
    supervisor._browser_pid = 4242
    supervisor._browser_process_start = 1000.0
    dials = 0

    async def connect(*_args, **_kwargs):
        nonlocal dials
        dials += 1
        if dials > 1:
            raise AssertionError("dead local browser must not be redialed")
        return _ClosingWebSocket()

    async def _noop(*_a, **_k):
        pass

    monkeypatch.setattr(websockets, "connect", connect)
    monkeypatch.setattr(supervisor, "_attach_initial_page", _noop)
    monkeypatch.setattr(supervisor, "_read_loop", _noop)
    monkeypatch.setattr(supervisor, "_capture_local_browser_process", lambda: None)
    monkeypatch.setattr(bs, "_local_listener_identity", lambda _url: (True, 4242))
    monkeypatch.setattr(psutil, "Process", lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))

    asyncio.run(asyncio.wait_for(supervisor._run(), timeout=2.0))
    assert dials == 1
    assert supervisor.snapshot().active is False
    assert bs.SUPERVISOR_REGISTRY.get(supervisor.task_id) is None


def test_missing_loopback_listener_is_dead_but_remote_keeps_budget(monkeypatch):
    local = bs.CDPSupervisor(task_id="missing-listener", cdp_url="ws://127.0.0.1:9222")
    remote = bs.CDPSupervisor(task_id="remote-budget", cdp_url="ws://browserbase.example")
    monkeypatch.setattr(bs, "_local_listener_identity", lambda url: None if "browserbase" in url else (False, None))
    assert local._local_browser_process_alive() is False
    assert remote._local_browser_process_alive() is True


def test_recycled_browser_pid_is_not_the_original_process(monkeypatch):
    import psutil
    supervisor = bs.CDPSupervisor(task_id="recycled-browser", cdp_url="ws://127.0.0.1:9222")
    supervisor._browser_pid = 4242
    supervisor._browser_process_start = 1000.0
    process = type("Process", (), {
        "is_running": lambda self: True,
        "status": lambda self: psutil.STATUS_RUNNING,
        "create_time": lambda self: 2000.0,
    })()
    monkeypatch.setattr(bs, "_local_listener_identity", lambda _url: (True, 4242))
    monkeypatch.setattr(psutil, "Process", lambda pid: process)
    assert supervisor._local_browser_process_alive() is False


def test_captured_pid_requires_present_matching_listener(monkeypatch):
    import psutil
    supervisor = bs.CDPSupervisor(task_id="listener-ownership", cdp_url="ws://127.0.0.1:9222")
    supervisor._browser_pid = 4242
    supervisor._browser_process_start = 1000.0
    process = type("Process", (), {
        "is_running": lambda self: True,
        "status": lambda self: psutil.STATUS_RUNNING,
        "create_time": lambda self: 1000.0,
    })()
    monkeypatch.setattr(psutil, "Process", lambda pid: process)
    for identity in ((False, None), (True, 5555), (True, None)):
        monkeypatch.setattr(bs, "_local_listener_identity", lambda _url, value=identity: value)
        assert supervisor._local_browser_process_alive() is False, identity
    monkeypatch.setattr(bs, "_local_listener_identity", lambda _url: (True, 4242))
    assert supervisor._local_browser_process_alive() is True


def test_wildcard_listener_matches_loopback_cdp(monkeypatch):
    import psutil
    from types import SimpleNamespace
    for host, bind_ip in (("127.0.0.1", "0.0.0.0"), ("::1", "::")):
        connection = SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(ip=bind_ip, port=9222), pid=4242)
        monkeypatch.setattr(psutil, "net_connections", lambda kind: [connection])
        url = f"ws://[{host}]:9222" if ":" in host else f"ws://{host}:9222"
        assert bs._local_listener_identity(url) == (True, 4242)


def test_uncaptured_unknown_local_listener_is_not_safe_to_reconnect(monkeypatch):
    supervisor = bs.CDPSupervisor(task_id="unknown-local-owner", cdp_url="ws://127.0.0.1:9222")
    monkeypatch.setattr(bs, "_local_listener_identity", lambda _url: (True, None))
    assert supervisor._local_browser_process_alive() is False


def test_reconnect_does_not_adopt_replacement_local_browser(monkeypatch, unregister):
    """Owner A can exit after the pre-dial check; owner B must not inherit its session."""
    import psutil
    supervisor = bs.CDPSupervisor(task_id="replacement-local-browser", cdp_url="ws://127.0.0.1:9222")
    bs.SUPERVISOR_REGISTRY._by_task[supervisor.task_id] = supervisor
    unregister.append(supervisor.task_id)
    listener_pid = 4242
    dials = 0
    attach_calls = 0

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def is_running(self):
            return True

        def status(self):
            return psutil.STATUS_RUNNING

        def create_time(self):
            return 1000.0 if self.pid == 4242 else 2000.0

    async def connect(*_args, **_kwargs):
        nonlocal dials, listener_pid
        dials += 1
        if dials > 2:
            raise AssertionError("replacement local browser must not be redialed")
        if dials == 2:
            listener_pid = 5555
        return _ClosingWebSocket()

    async def attach(*_args, **_kwargs):
        nonlocal attach_calls
        attach_calls += 1

    async def read_loop(*_args, **_kwargs):
        pass

    monkeypatch.setattr(websockets, "connect", connect)
    monkeypatch.setattr(supervisor, "_attach_initial_page", attach)
    monkeypatch.setattr(supervisor, "_read_loop", read_loop)
    monkeypatch.setattr(bs, "_local_listener_identity", lambda _url: (True, listener_pid))
    monkeypatch.setattr(psutil, "Process", Process)

    asyncio.run(asyncio.wait_for(supervisor._run(), timeout=2.0))
    assert dials == 2
    assert attach_calls == 1
    assert supervisor._browser_pid == 4242
    assert supervisor._browser_process_start == 1000.0
    assert supervisor.snapshot().active is False
    assert bs.SUPERVISOR_REGISTRY.get(supervisor.task_id) is None


def test_lsof_listener_fallback_handles_wildcards_and_missing_listener(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    responses = iter([
        SimpleNamespace(returncode=0, stdout="p4242\nn0.0.0.0:9222\n"),
        SimpleNamespace(returncode=1, stdout=""),
    ])
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: next(responses))
    assert bs._lsof_local_listener_identity(9222, {"127.0.0.1", "0.0.0.0", "*"}) == (True, 4242)
    assert bs._lsof_local_listener_identity(9222, {"127.0.0.1", "0.0.0.0", "*"}) == (False, None)


def test_process_identity_falls_back_to_ps_without_psutil(monkeypatch):
    import subprocess
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=0, stdout="Ss Sun Oct 25 08:00:00 2026\n",
    ))
    alive, started = bs._local_process_identity(4242)
    assert alive is True
    assert isinstance(started, float)
