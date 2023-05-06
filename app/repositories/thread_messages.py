from datetime import datetime
from typing import TypedDict, cast, Any

from app import state

READ_PARAMS = """\
    thread_message_id,
    content,
    role,
    tokens_used,
    created_at
"""


class ThreadMessage(TypedDict):
    thread_message_id: int
    thread_id: int
    content: str
    role: str
    tokens_used: int
    created_at: datetime


async def create(
    thread_id: int,
    content: str,
    role: str,
    tokens_used: int,
) -> ThreadMessage:
    query = f"""\
        INSERT INTO thread_messages (thread_id, content, role, tokens_used)
        VALUES (:thread_id, :content, :role, :tokens_used)
        RETURNING {READ_PARAMS}
    """
    values: dict[str, Any] = {
        "thread_id": thread_id,
        "content": content,
        "role": role,
        "tokens_used": tokens_used,
    }
    rec = await state.write_database.fetch_one(query, values)
    return cast(ThreadMessage, rec)


async def fetch_one(thread_message_id: int) -> ThreadMessage:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM thread_messages
        WHERE thread_message_id = :thread_message_id
    """
    values: dict[str, Any] = {"thread_message_id": thread_message_id}
    rec = await state.read_database.fetch_one(query, values)
    return cast(ThreadMessage, rec)


async def fetch_many(
    thread_id: int | None = None,
    role: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[ThreadMessage]:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM thread_messages
        WHERE thread_id = COALESCE(:thread_id, thread_id)
        AND role = COALESCE(:role, role)
    """
    values: dict[str, Any] = {"thread_id": thread_id, "role": role}
    if page is not None and page_size is not None:
        query += "LIMIT :page_size OFFSET :offset"
        values["page_size"] = page_size
        values["offset"] = (page - 1) * page_size
    recs = await state.read_database.fetch_all(query, values)
    return cast(list[ThreadMessage], recs)
