import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from app import state


async def create(
    *,
    thread_id: int,
    requester_id: int,
    model: str,
    usage: dict[str, Any] | None,
    estimated_cost_usd: Decimal | None,
) -> None:
    await state.write_database.execute(
        """
        INSERT INTO ai_requests (thread_id, requester_id, model, usage, estimated_cost_usd)
        VALUES (:thread_id, :requester_id, :model, :usage, :estimated_cost_usd)
        """,
        {
            "thread_id": thread_id,
            "requester_id": requester_id,
            "model": model,
            "usage": json.dumps(usage) if usage is not None else None,
            "estimated_cost_usd": estimated_cost_usd,
        },
    )


async def costs_by_requester(
    thread_id: int | None = None, created_at_gte: datetime | None = None
) -> list[dict[str, Any]]:
    return await state.read_database.fetch_all(
        """
        SELECT requester_id, COALESCE(SUM(estimated_cost_usd), 0) AS cost,
               COUNT(*) FILTER (WHERE estimated_cost_usd IS NULL) AS unpriced_requests
        FROM ai_requests
        WHERE thread_id = COALESCE(:thread_id, thread_id)
          AND created_at >= COALESCE(:created_at_gte, created_at)
        GROUP BY requester_id
        ORDER BY cost DESC
        """,
        {"thread_id": thread_id, "created_at_gte": created_at_gte},
    )


async def record_execution_cost(
    *, thread_id: int, requester_id: int, session_id: str, total_cost: Decimal
) -> None:
    # Persist the cumulative estimate and its newly attributed cost atomically.
    # Repeated observations of the same billing interval must not double count.
    await state.write_database.execute(
        """
        WITH previous AS (
            SELECT thread_id, execution_cost_usd
            FROM threads
            WHERE thread_id = :thread_id AND execution_session_id = :session_id
            FOR UPDATE
        ), updated AS (
            UPDATE threads t SET execution_cost_usd = :total_cost
            FROM previous p
            WHERE t.thread_id = p.thread_id AND :total_cost > p.execution_cost_usd
            RETURNING :total_cost - p.execution_cost_usd AS added_cost
        )
        INSERT INTO ai_requests
            (thread_id, requester_id, model, usage, estimated_cost_usd)
        SELECT :thread_id, :requester_id, 'openai-hosted-shell', :usage, added_cost
        FROM updated
        """,
        {
            "thread_id": thread_id,
            "requester_id": requester_id,
            "session_id": session_id,
            "total_cost": total_cost,
            "usage": json.dumps(
                {
                    "container_id": session_id,
                    "estimated_cumulative_cost_usd": str(total_cost),
                    "memory_limit": "1g",
                }
            ),
        },
    )
