import ssl
from types import TracebackType
from typing import Any
from typing import Type

from databases import Database as _Database
from databases.core import Connection
from databases.core import Transaction


def _create_pool(
    dsn: str,
    min_pool_size: int,
    max_pool_size: int,
    ssl: bool | ssl.SSLContext,
) -> _Database:
    return _Database(
        url=dsn,
        min_size=min_pool_size,
        max_size=max_pool_size,
        ssl=ssl,
    )


# TODO: refactor this to support dialect/driver separation,
#       and to leverage the robust urllib.parse.urlunparse.
def dsn(
    scheme: str,
    user: str,
    password: str,
    host: str,
    port: int,
    database: str,
) -> str:
    return f"{scheme}://{user}:{password}@{host}:{port}/{database}"


class Database:
    def __init__(
        self,
        dsn: str,
        db_ssl: bool | ssl.SSLContext,
        min_pool_size: int,
        max_pool_size: int,
    ) -> None:
        self.pool = _create_pool(
            dsn,
            min_pool_size,
            max_pool_size,
            db_ssl,
        )

    async def __aenter__(self) -> "Database":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_value: None | BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.disconnect()

    def connection(self) -> Connection:
        return self.pool.connection()

    def transaction(
        self,
        *,
        force_rollback: bool = False,
        **kwargs: Any,
    ) -> Transaction:
        return self.pool.transaction(
            force_rollback=force_rollback,
            **kwargs,
        )

    async def connect(self) -> None:
        await self.pool.connect()
        await self.pool.connect()

    async def disconnect(self) -> None:
        await self.pool.disconnect()
        await self.pool.disconnect()

    async def fetch_one(
        self,
        query: str,
        values: dict | None = None,
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            rec = await connection.fetch_one(query, values)

        return dict(rec._mapping) if rec is not None else None

    async def fetch_all(
        self,
        query: str,
        values: dict | None = None,
    ) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            recs = await connection.fetch_all(query, values)

        return [dict(rec._mapping) for rec in recs]

    async def fetch_val(self, query: str, values: dict | None = None) -> Any:
        async with self.pool.connection() as connection:
            val = await connection.fetch_val(query, values)

        return val

    async def execute(self, query: str, values: dict | None = None) -> Any:
        async with self.pool.connection() as connection:
            result = await connection.execute(query, values)

        return result

    async def execute_many(self, query: str, values: list) -> None:
        async with self.pool.connection() as connection:
            await connection.execute_many(query, values)

        return None
