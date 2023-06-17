import json
from datetime import datetime
from typing import Any
from typing import cast
from typing import Literal
from typing import TypedDict

from app import state
from app._typing import UNSET
from app._typing import Unset

READ_PARAMS = """\
    thread_message_id,
    content,
    role,
    tokens_used,
    function_name,
    function_args,
    created_at
"""


class ThreadMessage(TypedDict):
    thread_message_id: int
    thread_id: int
    content: str | None
    role: Literal["user", "assistant", "function", "system"]
    tokens_used: int
    function_name: str | None
    function_args: Any | None
    created_at: datetime


async def create(
    thread_id: int,
    content: str | None,
    role: Literal["user", "assistant", "function", "system"],
    tokens_used: int,
    function_name: str | None = None,
    function_args: Any | None = None,
) -> ThreadMessage:
    query = f"""\
        INSERT INTO thread_messages (thread_id, content, role, tokens_used, function_name,
                                     function_args)
        VALUES (:thread_id, :content, :role, :tokens_used, :function_name,
                :function_args)
        RETURNING {READ_PARAMS}
    """
    values: dict[str, Any] = {
        "thread_id": thread_id,
        "content": content,
        "role": role,
        "tokens_used": tokens_used,
        "function_name": function_name,
        "function_args": (
            json.dumps(function_args) if function_args is not None else None
        ),
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
    thread_id: int | Unset = UNSET,
    role: Literal["user", "assistant", "function", "system"] | Unset = UNSET,
    function_name: str | None | Unset = UNSET,
    page: int | None = None,
    page_size: int | None = None,
) -> list[ThreadMessage]:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM thread_messages
    """
    values: dict[str, Any] = {}

    filters = []
    if not isinstance(thread_id, Unset):
        filters.append("thread_id = :thread_id")
        values["thread_id"] = thread_id
    if not isinstance(role, Unset):
        filters.append("role = :role")
        values["role"] = role
    if not isinstance(function_name, Unset):
        filters.append("function_name = :function_name")
        values["function_name"] = function_name
    if filters:
        query += " WHERE " + " AND ".join(filters) + " "

    if page is not None and page_size is not None:
        query += "LIMIT :page_size OFFSET :offset"
        values["page_size"] = page_size
        values["offset"] = (page - 1) * page_size

    recs = await state.read_database.fetch_all(query, values)
    return cast(list[ThreadMessage], recs)
