from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PersonalMessage(BaseModel):
    id: int
    user_id: int
    content: str
    role: Literal["user", "assistant"]
    tokens_used: int
    created_at: datetime