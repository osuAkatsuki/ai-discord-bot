from httpx import AsyncClient

from app import settings
from app.adapters import database
from app.adapters.database import Database

read_database = database.Database(
    database.dsn(
        scheme="postgresql",
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


write_database = database.Database(
    database.dsn(
        scheme="postgresql",
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


akatsuki_read_database = database.Database(
    database.dsn(
        scheme="mysql",
        user=settings.AKATSUKI_READ_DB_USER,
        password=settings.AKATSUKI_READ_DB_PASS,
        host=settings.AKATSUKI_READ_DB_HOST,
        port=settings.AKATSUKI_READ_DB_PORT,
        database=settings.AKATSUKI_READ_DB_NAME,
    ),
    db_ssl=settings.AKATSUKI_READ_DB_USE_SSL,
    min_pool_size=settings.DB_POOL_MIN_SIZE,
    max_pool_size=settings.DB_POOL_MAX_SIZE,
)

http_client: AsyncClient
