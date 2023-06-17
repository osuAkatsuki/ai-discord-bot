from collections.abc import Sequence
from typing import Any
from typing import Literal
from typing import Required
from typing import TypedDict

import backoff
import openai.error
from openai.openai_object import OpenAIObject

from app.repositories.thread_messages import ThreadMessage


class FunctionCall(TypedDict):
    # "function_call": {
    #   "name": "ask_database",
    #   "arguments": "{\n  \"query\": \"SELECT * FROM users ORDER BY id DESC LIMIT 5\"\n}"
    # }
    name: str
    arguments: str


class Message(TypedDict, total=False):
    role: Required[Literal["user", "assistant", "function", "system"]]
    content: Required[str | None]  # null if there's a function call
    function_call: FunctionCall  # only exists when role is "function"
    name: str  # only exists when role is "function"


class Property(TypedDict, total=False):
    type: Required[str]
    enum: list[str]  # optional
    description: Required[str]


class Parameters(TypedDict):
    type: str
    properties: dict[str, Property]
    required: list[str]


class FunctionSchema(TypedDict):
    name: str
    description: str
    parameters: Parameters


class GPTRequest(TypedDict, total=False):
    model: Required[str]
    messages: Required[Sequence[Message]]
    functions: Sequence[FunctionSchema]


class GPTResponse(TypedDict):
    # TODO
    ...


MAX_BACKOFF_TIME = 16


def _is_non_retriable_error(error: Exception) -> bool:
    """\
    Determine whether an error is non-retriable.
    """
    if isinstance(error, openai.error.APIConnectionError):
        return error.should_retry  # TODO: confirm this
    elif isinstance(
        error,
        (
            openai.error.APIError,  # TODO: confirm this
            openai.error.TryAgain,
            openai.error.Timeout,
            openai.error.RateLimitError,
            openai.error.ServiceUnavailableError,
        ),
    ):
        return True
    elif isinstance(
        error,
        (
            openai.error.InvalidRequestError,
            openai.error.AuthenticationError,
            openai.error.PermissionError,
            openai.error.InvalidAPIType,
            openai.error.SignatureVerificationError,
        ),
    ):
        return False
    else:
        raise NotImplementedError(f"Unknown error type: {error}")


def serialize(
    model: str,
    thread_messages: Sequence[ThreadMessage],
    functions: Sequence[FunctionSchema] | None = None,
) -> GPTRequest:
    """\
    Convert a sequence of thread messages to the format expected by the OpenAI API.
    """
    if model == "gpt-4":
        model = "gpt-4-0613"

    messages: list[Message] = []
    for thread_message in thread_messages:
        contruction: dict[str, Any] = {
            "role": thread_message["role"],
            "content": thread_message["content"],
        }
        if (
            # assistant is invoking a function of ours
            thread_message["role"] == "assistant"
            and thread_message["function_name"] is not None
            and thread_message["function_args"] is not None
        ):
            contruction["function_call"] = {
                "name": thread_message["function_name"],
                "arguments": thread_message["function_args"],
            }
        elif thread_message["role"] == "function":
            contruction["name"] = thread_message["function_name"]

        message = Message(**contruction)
        messages.append(message)

    gpt_request: GPTRequest = {
        "model": model,
        "messages": messages,
    }
    if functions is not None:
        gpt_request["functions"] = functions

    return gpt_request


def deserialize(gpt_response: GPTResponse) -> Sequence[ThreadMessage]:
    """\
    Convert a GPTResponse to a sequence of thread messages.
    """
    raise NotImplementedError("Still need to write this")


@backoff.on_exception(
    backoff.expo,
    openai.error.OpenAIError,
    max_time=MAX_BACKOFF_TIME,
    giveup=_is_non_retriable_error,
)
async def send(
    model: str,
    thread_messages: Sequence[ThreadMessage],
    functions: Sequence[FunctionSchema] | None = None,
) -> OpenAIObject:
    """\
    Send a message to the OpenAI API, as a given model.

    https://beta.openai.com/docs/api-reference/create-completion
    """
    request = serialize(model, thread_messages, functions)
    response = await openai.ChatCompletion.acreate(**request)
    assert isinstance(response, OpenAIObject), "this should never fail"
    return response
