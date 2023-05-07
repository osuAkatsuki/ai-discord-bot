from datetime import datetime
from typing import TypedDict, cast, Any

from app import state

READ_PARAMS = """\
    thread_id,
    initiator_user_id,
    model,
    created_at
"""


class Thread(TypedDict):
    thread_id: int
    initiator_user_id: int
    model: str
    created_at: datetime


async def create(
    thread_id: int,
    initiator_user_id: int,
    model: str,
) -> Thread:
    query = f"""\
        INSERT INTO threads (thread_id, initiator_user_id, model)
        VALUES (:thread_id, :initiator_user_id, :model)
        RETURNING {READ_PARAMS}
    """
    values: dict[str, Any] = {
        "thread_id": thread_id,
        "initiator_user_id": initiator_user_id,
        "model": model,
    }
    rec = await state.write_database.fetch_one(query, values)
    return cast(Thread, rec)


async def fetch_one(thread_id: int) -> Thread:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM threads
        WHERE thread_id = :thread_id
    """
    values: dict[str, Any] = {"thread_id": thread_id}
    rec = await state.read_database.fetch_one(query, values)
    return cast(Thread, rec)


async def fetch_many(
    initiator_user_id: int | None = None,
    model: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[Thread]:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM threads
        WHERE initiator_user_id = COALESCE(:initiator_user_id, initiator_user_id)
        AND model = COALESCE(:model, model)
    """
    values: dict[str, Any] = {
        "initiator_user_id": initiator_user_id,
        "model": model,
    }
    if page is not None and page_size is not None:
        query += "LIMIT :page_size OFFSET :offset"
        values["page_size"] = page_size
        values["offset"] = (page - 1) * page_size
    recs = await state.read_database.fetch_all(query, values)
    return cast(list[Thread], recs)
