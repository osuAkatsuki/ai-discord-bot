import httpx

from app import settings
from app import state
from app.adapters import database


async def start() -> None:
    state.read_database = database.Database(
        database.dsn(
            scheme=settings.READ_DB_SCHEME,
            user=settings.READ_DB_USER,
            password=settings.READ_DB_PASS,
            host=settings.READ_DB_HOST,
            port=settings.READ_DB_PORT,
            database=settings.READ_DB_NAME,
        ),
        db_ssl=settings.READ_DB_USE_SSL,
        min_pool_size=settings.DB_POOL_MIN_SIZE,
        max_pool_size=settings.DB_POOL_MAX_SIZE,
    )
    await state.read_database.connect()

    state.write_database = database.Database(
        database.dsn(
            scheme=settings.WRITE_DB_SCHEME,
            user=settings.WRITE_DB_USER,
            password=settings.WRITE_DB_PASS,
            host=settings.WRITE_DB_HOST,
            port=settings.WRITE_DB_PORT,
            database=settings.WRITE_DB_NAME,
        ),
        db_ssl=settings.WRITE_DB_USE_SSL,
        min_pool_size=settings.DB_POOL_MIN_SIZE,
        max_pool_size=settings.DB_POOL_MAX_SIZE,
    )
    await state.write_database.connect()

    state.http_client = httpx.AsyncClient()


async def stop() -> None:
    await state.http_client.aclose()
    await state.write_database.disconnect()
    await state.read_database.disconnect()
