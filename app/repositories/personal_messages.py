from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal
from typing import Mapping

from pydantic import BaseModel

from app import state

READ_PARAMS = """\
    id,
    user_id,
    content,
    role,
    tokens_used,
    created_at
"""


class PersonalMessage(BaseModel):
    id: int
    user_id: int
    content: str
    role: Literal["user", "assistant"]
    tokens_used: int
    created_at: datetime


def deserialize(record: Mapping[str, Any]) -> PersonalMessage:
    return PersonalMessage(
        id=record["id"],
        user_id=record["user_id"],
        content=record["content"],
        role=record["role"],
        tokens_used=record["tokens_used"],
        created_at=record["created_at"],
    )


async def create(
    user_id: int,
    content: str,
    role: Literal["user", "assistant"],
    tokens_used: int,
) -> PersonalMessage:
    query = f"""\
        INSERT INTO personal_messages (user_id, content, role, tokens_used)
        VALUES (:user_id, :content, :role, :tokens_used)
        RETURNING {READ_PARAMS}
    """
    values: dict[str, Any] = {
        "user_id": user_id,
        "content": content,
        "role": role,
        "tokens_used": tokens_used,
    }
    record = await state.write_database.fetch_one(query=query, values=values)
    assert record is not None
    return deserialize(record)


async def fetch_one(personal_message_id: int) -> PersonalMessage | None:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM personal_messages
        WHERE id = :personal_message_id
    """
    values = {"personal_message_id": personal_message_id}
    record = await state.read_database.fetch_one(query=query, values=values)
    return deserialize(record) if record is not None else None


async def fetch_last_n(user_id: int, n: int) -> list[PersonalMessage]:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM personal_messages
        WHERE user_id = :user_id
        ORDER BY created_at DESC
        LIMIT :n
    """
    values = {"user_id": user_id, "n": n}
    records = await state.read_database.fetch_all(query=query, values=values)
    return [deserialize(record) for record in records]


async def fetch_created_before(user_id: int, created_at: datetime) -> list[PersonalMessage]:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM personal_messages
        WHERE user_id = :user_id AND created_at < :created_at
    """
    values = {"user_id": user_id, "created_at": created_at}
    records = await state.read_database.fetch_all(query=query, values=values)
    return [deserialize(record) for record in records]


async def delete_from_user_id(user_id: int) -> int:
    query = f"""\
        DELETE FROM personal_messages
        WHERE user_id = :user_id
    """
    values = {"user_id": user_id}
    return await state.write_database.execute(query=query, values=values)
