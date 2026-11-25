"""The FIFO claim's pending-row reads wait off the owner loop.

Every turn's drain reads the session's pending admissions before its claim (and re-reads the head
after an awaited preclaim check). On slow storage that read used to stop the whole owner loop, every
other session's turn, Stop and socket included. Real SessionAuthority + state.db; only the session
under test's read is blocked, and an independent ticker must run before that read may finish. The
claim still takes the head the read validated.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from tests.fixtures.storage_seed import seed_session
from tests.gateway.fixtures.storage_handles import close_storage_handles, storage_handle


@pytest.mark.asyncio
async def test_claim_pending_read_keeps_the_owner_loop_ticking(tmp_path, monkeypatch):
    from gateway.session_authority import LiveSession, SessionAuthority
    from gateway.session_contract import SessionRef
    from persistence.sqlite.authority.admissions import SQLiteAdmissionRepository
    db = storage_handle(tmp_path)
    seed_session(db, 's', 'cron')
    authority = SessionAuthority(SimpleNamespace(_draining=False, adapters={}), profile_id='p', instance_id='t',
                                 persistence=db, epoch=db.runtime.begin_epoch(instance_id='t'))
    live = authority.sessions['s'] = LiveSession(SimpleNamespace(platform=None, user_id='u'), 'route')
    row, _ = db.admissions.admit(epoch=authority.epoch, principal_id='u', session_id='s',
                                 request_id='r1', payload={'text': 'x'})
    real = SQLiteAdmissionRepository.list
    read_started, release_read = threading.Event(), threading.Event()

    def slow_list(*args, **kwargs):
        read_started.set()
        # An event, not a sleep: full-suite CPU scheduling must not decide whether this test passes.
        assert release_read.wait(30), 'test did not release the blocked admission read'
        return real(*args, **kwargs)

    monkeypatch.setattr(SQLiteAdmissionRepository, 'list', slow_list)
    ticked = asyncio.Event()

    async def tick():
        while True:
            await asyncio.sleep(0.01)  # health: allow HX013 -- periodic independent owner-loop work
            ticked.set()

    ticker = asyncio.create_task(tick())
    claim = None
    claimed = first = None
    try:
        claim = asyncio.create_task(authority._claim_next(SessionRef('p', 's'), live))
        started = await asyncio.wait_for(asyncio.to_thread(read_started.wait, 10), 15)
        assert started, 'the admission read did not start'
        ticked.clear()
        # If list() runs on the event loop, no tick can happen until release_read's 30 s timeout;
        # claim then finishes and the blocked-read assertion below fails. With to_thread(), the
        # next tick arrives while the worker is still blocked, regardless of machine speed.
        await asyncio.wait_for(ticked.wait(), 15)
        assert not claim.done(), 'claim completed while its admission read was still blocked'
    finally:
        release_read.set()
        if claim is not None:
            claimed, first = await asyncio.wait_for(claim, 30)
        ticker.cancel()
        db.close()
    assert first is not None and first['admission_id'] == row.admission_id
    assert claimed and claimed['admission_id'] == row.admission_id and claimed['status'] == 'started'
