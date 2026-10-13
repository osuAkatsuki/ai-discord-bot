import os
from pathlib import Path

from dotenv import dotenv_values

# Supply settings without loading a local .env; preserve the test runner's database.
for key, value in dotenv_values(Path(__file__).parents[1] / ".env.example").items():
    os.environ.setdefault(key, value or "")
os.environ["OPENAI_API_KEY"] = "test-key"
