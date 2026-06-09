from datetime import datetime
from typing import Any

import pytest

from app import state
from app.adapters import database
from app.repositories import thread_messages


class _FakePool:
    def __init__(self) -> None:
        self.connect_calls = 0
        self.disconnect_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


@pytest.mark.asyncio
async def test_database_connect_and_disconnect_call_pool_once():
    pool = _FakePool()
    db = object.__new__(database.Database)
    db.pool = pool

    await db.connect()
    await db.disconnect()

    assert pool.connect_calls == 1
    assert pool.disconnect_calls == 1


class _FakeReadDatabase:
    def __init__(self) -> None:
        self.query: str | None = None
        self.values: dict[str, Any] | None = None

    async def fetch_all(
        self,
        query: str,
        values: dict[str, Any],
    ) -> list[dict[str, Any]]:
        self.query = " ".join(query.split())
        self.values = values
        return []


@pytest.mark.asyncio
async def test_thread_message_fetch_many_orders_before_pagination(monkeypatch):
    read_database = _FakeReadDatabase()
    monkeypatch.setattr(state, "read_database", read_database, raising=False)
    created_at_gte = datetime(2026, 1, 1)

    await thread_messages.fetch_many(
        thread_id=123,
        created_at_gte=created_at_gte,
        page=2,
        page_size=50,
    )

    assert read_database.query is not None
    assert (
        "created_at >= :created_at_gte "
        "ORDER BY created_at ASC, thread_message_id ASC "
        "LIMIT :page_size OFFSET :offset"
    ) in read_database.query
    assert read_database.values == {
        "thread_id": 123,
        "discord_user_id": None,
        "role": None,
        "created_at_gte": created_at_gte,
        "page_size": 50,
        "offset": 50,
    }


@pytest.mark.asyncio
async def test_thread_message_fetch_many_can_page_latest_messages(monkeypatch):
    read_database = _FakeReadDatabase()
    monkeypatch.setattr(state, "read_database", read_database, raising=False)

    await thread_messages.fetch_many(
        thread_id=123,
        page=1,
        page_size=10,
        sort_order="desc",
    )

    assert read_database.query is not None
    assert (
        "ORDER BY created_at DESC, thread_message_id DESC "
        "LIMIT :page_size OFFSET :offset"
    ) in read_database.query
    assert read_database.values == {
        "thread_id": 123,
        "discord_user_id": None,
        "role": None,
        "page_size": 10,
        "offset": 0,
    }
