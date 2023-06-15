from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Literal
from typing import Required
from typing import TypedDict

import backoff
import openai.error
from openai.openai_object import OpenAIObject


class Message(TypedDict, total=False):
    role: Required[Literal["user", "assistant", "function"]]
    content: Required[str]
    name: str  # only exists when role is "function"


class Property(TypedDict, total=False):
    type: Required[str]
    enum: list[str]  # optional
    description: Required[str]


class Parameters(TypedDict):
    type: str
    properties: dict[str, Property]
    required: list[str]


class Function(TypedDict):
    name: str
    description: str
    parameters: Parameters


class GPTResponse(TypedDict):
    choices: Sequence[Message]


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


@backoff.on_exception(
    backoff.expo,
    openai.error.OpenAIError,
    max_time=MAX_BACKOFF_TIME,
    giveup=_is_non_retriable_error,
)
async def send(
    model: str,
    messages: Sequence[Message],
    functions: Sequence[Function] | None = None,
) -> OpenAIObject:
    """\
    Send a message to the OpenAI API, as a given model.

    https://beta.openai.com/docs/api-reference/create-completion
    """
    if model == "gpt-4":  # add function calling support
        model = "gpt-4-0613"

    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    if functions is not None:
        kwargs["functions"] = functions

    response = await openai.ChatCompletion.acreate(**kwargs)
    assert isinstance(response, OpenAIObject)
    return response
