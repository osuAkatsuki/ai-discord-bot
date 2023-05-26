import backoff
import openai
from typing import Literal

import openai.error
from openai.openai_object import OpenAIObject

from typing import TypedDict, Sequence


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: str


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
) -> OpenAIObject:
    """\
    Send a message to the OpenAI API, as a given model.

    https://beta.openai.com/docs/api-reference/create-completion
    """
    response = await openai.ChatCompletion.acreate(model, messages)
    assert isinstance(response, OpenAIObject)
    return response
