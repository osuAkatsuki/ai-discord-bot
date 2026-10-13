from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app import state
from app.ai import AIModel

READ_PARAMS = """\
    thread_id,
    initiator_user_id,
    model,
    context_length,
    reasoning_effort,
    web_search,
    cloud_execution,
    execution_session_id,
    created_at
"""


class Thread(BaseModel):
    thread_id: int
    initiator_user_id: int
    model: AIModel
    context_length: int
    reasoning_effort: str | None = None
    web_search: bool = False
    cloud_execution: bool = False
    execution_session_id: str | None = None
    created_at: datetime


def deserialize(rec: Mapping[str, Any]) -> Thread:
    return Thread(
        thread_id=rec["thread_id"],
        initiator_user_id=rec["initiator_user_id"],
        model=AIModel(rec["model"]),
        context_length=rec["context_length"],
        created_at=rec["created_at"],
        reasoning_effort=rec["reasoning_effort"],
        web_search=rec["web_search"],
        cloud_execution=rec["cloud_execution"],
        execution_session_id=rec["execution_session_id"],
    )


async def create(
    thread_id: int,
    initiator_user_id: int,
    model: AIModel,
    context_length: int,
    reasoning_effort: str | None = None,
    web_search: bool = False,
    cloud_execution: bool = False,
) -> Thread:
    query = f"""\
        INSERT INTO threads (thread_id, initiator_user_id, model, context_length, reasoning_effort, web_search, cloud_execution)
        VALUES (:thread_id, :initiator_user_id, :model, :context_length, :reasoning_effort, :web_search, :cloud_execution)
        RETURNING {READ_PARAMS}
    """
    values: dict[str, Any] = {
        "thread_id": thread_id,
        "initiator_user_id": initiator_user_id,
        "model": model.value,
        "context_length": context_length,
        "reasoning_effort": reasoning_effort,
        "web_search": web_search,
        "cloud_execution": cloud_execution,
    }
    rec = await state.write_database.fetch_one(query, values)
    assert rec is not None
    return deserialize(rec)


async def fetch_one(thread_id: int) -> Thread | None:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM threads
        WHERE thread_id = :thread_id
    """
    values: dict[str, Any] = {"thread_id": thread_id}
    rec = await state.read_database.fetch_one(query, values)
    return deserialize(rec) if rec is not None else None


async def fetch_many(
    initiator_user_id: int | None = None,
    model: AIModel | None = None,
    context_length: int | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[Thread]:
    query = f"""\
        SELECT {READ_PARAMS}
        FROM threads
        WHERE initiator_user_id = COALESCE(:initiator_user_id, initiator_user_id)
        AND model = COALESCE(:model, model)
        AND context_length = COALESCE(:context_length, context_length)
    """
    values: dict[str, Any] = {
        "initiator_user_id": initiator_user_id,
        "model": model.value if model else None,
        "context_length": context_length,
    }
    if page is not None and page_size is not None:
        query += "LIMIT :page_size OFFSET :offset"
        values["page_size"] = page_size
        values["offset"] = (page - 1) * page_size
    recs = await state.read_database.fetch_all(query, values)
    return [deserialize(rec) for rec in recs]


async def partial_update(
    thread_id: int,
    initiator_user_id: int | None = None,
    model: AIModel | None = None,
    context_length: int | None = None,
) -> Thread | None:
    query = f"""\
        UPDATE threads
        SET initiator_user_id = COALESCE(:initiator_user_id, initiator_user_id),
            model = COALESCE(:model, model),
            context_length = COALESCE(:context_length, context_length)
        WHERE thread_id = :thread_id
        RETURNING {READ_PARAMS}
    """
    values: dict[str, Any] = {
        "thread_id": thread_id,
        "initiator_user_id": initiator_user_id,
        "model": model.value if model else None,
        "context_length": context_length,
    }
    rec = await state.write_database.fetch_one(query, values)
    return deserialize(rec) if rec is not None else None


async def set_model_options(
    thread_id: int, model: AIModel, reasoning_effort: str | None, web_search: bool
) -> None:
    await state.write_database.execute(
        """
        UPDATE threads SET model = :model, reasoning_effort = :reasoning_effort,
                           web_search = :web_search
        WHERE thread_id = :thread_id
        """,
        {
            "thread_id": thread_id,
            "model": model.value,
            "reasoning_effort": reasoning_effort,
            "web_search": web_search,
        },
    )


async def set_cloud_execution(thread_id: int, enabled: bool) -> None:
    await state.write_database.execute(
        "UPDATE threads SET cloud_execution = :enabled WHERE thread_id = :thread_id",
        {"thread_id": thread_id, "enabled": enabled},
    )


async def set_execution_session(thread_id: int, session_id: str | None) -> None:
    await state.write_database.execute(
        """
        UPDATE threads SET execution_session_id = :session_id,
                           execution_cost_usd = 0
        WHERE thread_id = :thread_id
        """,
        {"thread_id": thread_id, "session_id": session_id},
    )
