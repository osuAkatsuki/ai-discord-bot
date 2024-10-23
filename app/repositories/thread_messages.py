from datetime import datetime
from typing import Any
from typing import Literal
from typing import TypedDict

from app import state

READ_PARAMS = """\
    thread_message_id,
    thread_id,
    content,
    discord_user_id,
    role,
    tokens_used,
    created_at
"""


class ThreadMessage(TypedDict):
    thread_message_id: int
    thread_id: int
    content: str
    discord_user_id: int
    role: Literal["user", "assistant"]
    tokens_used: int
    created_at: datetime


def deserialize(rec: dict[str, Any]) -> ThreadMessage:
    return {
        "thread_message_id": rec["thread_message_id"],
        "thread_id": rec["thread_id"],
        "content": rec["content"],
        "discord_user_id": rec["discord_user_id"],
        "role": rec["role"],
        "tokens_used": rec["tokens_used"],
        "created_at": rec["created_at"],
    }


async def create(
    thread_id: int,
    content: str,
    discord_user_id: int,
    role: Literal["user", "assistant"],
    tokens_used: int,
) -> ThreadMessage:
    query = f"""\
        INSERT INTO thread_messages (thread_id, content, discord_user_id, role, tokens_used)
        VALUES (:thread_id, :content, :discord_user_id, :role, :tokens_used)
        RETURNING {READ_PARAMS}
    """
    values: dict[str, Any] = {
        "thread_id": thread_id,
        "content": content,
        "discord_user_id": discord_user_id,
        "role": role,
        "tokens_used": tokens_used,
    }
    rec = await state.write_database.fetch_one(query, values)
    assert rec is not None
    return deserialize(rec)


async def fetch_one(thread_message_id: int) -> ThreadMessage | None:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM thread_messages
        WHERE thread_message_id = :thread_message_id
    """
    values: dict[str, Any] = {"thread_message_id": thread_message_id}
    rec = await state.read_database.fetch_one(query, values)
    return deserialize(rec) if rec is not None else None


async def fetch_many(
    thread_id: int | None = None,
    discord_user_id: int | None = None,
    role: Literal["user", "assistant"] | None = None,
    created_at_gte: datetime | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[ThreadMessage]:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM thread_messages
        WHERE thread_id = COALESCE(:thread_id, thread_id)
        AND discord_user_id = COALESCE(:discord_user_id, discord_user_id)
        AND role = COALESCE(:role, role)
    """
    values: dict[str, Any] = {
        "thread_id": thread_id,
        "discord_user_id": discord_user_id,
        "role": role,
    }
    if created_at_gte is not None:
        query += "AND created_at >= :created_at_gte"
        values["created_at_gte"] = created_at_gte
    if page is not None and page_size is not None:
        query += "LIMIT :page_size OFFSET :offset"
        values["page_size"] = page_size
        values["offset"] = (page - 1) * page_size
    recs = await state.read_database.fetch_all(query, values)
    return [deserialize(rec) for rec in recs]
