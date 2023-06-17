import os

from dotenv import load_dotenv


def read_bool(value: str) -> bool:
    return value.lower() == "true"  # keep it simple


def read_list(value: str) -> list[str]:
    return value.split(",")


load_dotenv()


APP_ENV = os.environ["APP_ENV"]
APP_COMPONENT = os.environ["APP_COMPONENT"]

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
GOOGLE_PLACES_API_KEY = os.environ["GOOGLE_PLACES_API_KEY"]

READ_DB_SCHEME = os.environ["READ_DB_SCHEME"]
READ_DB_HOST = os.environ["READ_DB_HOST"]
READ_DB_PORT = int(os.environ["READ_DB_PORT"])
READ_DB_USER = os.environ["READ_DB_USER"]
READ_DB_PASS = os.environ["READ_DB_PASS"]
READ_DB_NAME = os.environ["READ_DB_NAME"]
READ_DB_USE_SSL = read_bool(os.environ["READ_DB_USE_SSL"])
READ_DB_CA_CERTIFICATE = os.environ["READ_DB_CA_CERTIFICATE"]
INITIALLY_AVAILABLE_READ_DB = os.environ["INITIALLY_AVAILABLE_READ_DB"]

WRITE_DB_SCHEME = os.environ["WRITE_DB_SCHEME"]
WRITE_DB_HOST = os.environ["WRITE_DB_HOST"]
WRITE_DB_PORT = int(os.environ["WRITE_DB_PORT"])
WRITE_DB_USER = os.environ["WRITE_DB_USER"]
WRITE_DB_PASS = os.environ["WRITE_DB_PASS"]
WRITE_DB_NAME = os.environ["WRITE_DB_NAME"]
WRITE_DB_USE_SSL = read_bool(os.environ["WRITE_DB_USE_SSL"])
WRITE_DB_CA_CERTIFICATE = os.environ["WRITE_DB_CA_CERTIFICATE"]
INITIALLY_AVAILABLE_WRITE_DB = os.environ["INITIALLY_AVAILABLE_WRITE_DB"]

AKATSUKI_READ_DB_SCHEME = os.environ["AKATSUKI_READ_DB_SCHEME"]
AKATSUKI_READ_DB_HOST = os.environ["AKATSUKI_READ_DB_HOST"]
AKATSUKI_READ_DB_PORT = int(os.environ["AKATSUKI_READ_DB_PORT"])
AKATSUKI_READ_DB_USER = os.environ["AKATSUKI_READ_DB_USER"]
AKATSUKI_READ_DB_PASS = os.environ["AKATSUKI_READ_DB_PASS"]
AKATSUKI_READ_DB_NAME = os.environ["AKATSUKI_READ_DB_NAME"]
AKATSUKI_READ_DB_USE_SSL = read_bool(os.environ["AKATSUKI_READ_DB_USE_SSL"])
AKATSUKI_READ_DB_CA_CERTIFICATE = os.environ["AKATSUKI_READ_DB_CA_CERTIFICATE"]

# TODO: per-database settings?
DB_POOL_MIN_SIZE = int(os.environ["DB_POOL_MIN_SIZE"])
DB_POOL_MAX_SIZE = int(os.environ["DB_POOL_MAX_SIZE"])

SERVICE_READINESS_TIMEOUT = int(os.environ["SERVICE_READINESS_TIMEOUT"])

AKATSUKI_DB_SCHEMA_TABLES = read_list(os.environ["AKATSUKI_DB_SCHEMA_TABLES"])
