from enum import StrEnum

from pydantic import BaseModel


class ErrorCode(StrEnum):
    UNEXPECTED_ERROR = "unexpected_error"
    INVALID_INPUT = "invalid_input"
    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    NOT_READY = "not_ready"
    USER_ERROR = "user_error"
    SKIP = "skip"


class Error(BaseModel):
    code: ErrorCode
    messages: list[str]
