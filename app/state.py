from httpx import AsyncClient

from app.adapters.database import Database

read_database: Database
write_database: Database

http_client: AsyncClient
