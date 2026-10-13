import asyncio
from weakref import WeakValueDictionary

from app import ai
from app.repositories import ai_requests
from app.repositories import threads

# One bot process owns the Discord connection. Weak references release idle locks.
_thread_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def thread_lock(thread_id: int) -> asyncio.Lock:
    lock = _thread_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _thread_locks[thread_id] = lock
    return lock


async def prepare_session(
    thread: threads.Thread, requester_id: int
) -> tuple[ai.ExecutionSession, str | None]:
    ai.validate_execution(thread.model)
    session = None
    notice = None
    if thread.execution_session_id:
        session = await ai.get_execution_session(thread.execution_session_id)
        if session is None:
            notice = (
                "The previous cloud workspace expired or its network policy changed. "
                "A fresh workspace was created; previous files and installed packages "
                "are unavailable. Reattach any files you need."
            )
    if session is None:
        session = await ai.create_execution_session()
        try:
            await threads.set_execution_session(thread.thread_id, session.id)
        except Exception:
            await ai.delete_execution_session(session.id)
            raise
    await record_cost(thread.thread_id, requester_id, session)
    return session, notice


async def record_cost(
    thread_id: int, requester_id: int, session: ai.ExecutionSession
) -> None:
    await ai_requests.record_execution_cost(
        thread_id=thread_id,
        requester_id=requester_id,
        session_id=session.id,
        total_cost=ai.execution_session_cost(session),
    )


async def configure(thread: threads.Thread, enabled: bool, reset: bool) -> None:
    if enabled:
        ai.validate_execution(thread.model)
    if thread.execution_session_id and (reset or not enabled):
        await ai.delete_execution_session(thread.execution_session_id)
        await threads.set_execution_session(thread.thread_id, None)
    await threads.set_cloud_execution(thread.thread_id, enabled)
