from collections.abc import Sequence
from enum import StrEnum
from typing import Any
from typing import Literal
from typing import Required
from typing import TypeAlias
from typing import TypedDict

import openai
from openai.types.chat import ChatCompletion

from app import settings


openai_client = openai.AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
)


class OpenAIModel(StrEnum):
    GPT_4_0125_PREVIEW = "gpt-4-0125-preview"
    GPT_4_1106_PREVIEW = "gpt-4-1106-preview"
    GPT_4_1106_VISION_PREVIEW = "gpt-4-1106-vision-preview"

    GPT_4 = "gpt-4"
    GPT_4_OMNI = "gpt-4o"
    GPT_4_32K = "gpt-4-32k"

    GPT_3_5_TURBO_0125 = "gpt-3.5-turbo-0125"
    GPT_3_5_TURBO_INSTRUCT = "gpt-3.5-turbo-instruct"
    GPT_3_5_TURBO_1106 = "gpt-3.5-turbo-1106"
    GPT_3_5_TURBO_0613 = "gpt-3.5-turbo-0613"
    GPT_3_5_TURBO_16K_0613 = "gpt-3.5-turbo-16k-0613"
    GPT_3_5_TURBO_0301 = "gpt-3.5-turbo-0301"

    # pointers to the latest model from a given class
    GPT_4_TURBO_PREVIEW = "gpt-4-turbo-preview"
    GPT_3_5_TURBO = "gpt-3.5-turbo"


class TextMessage(TypedDict):
    type: Literal["text"]
    text: str


class ImageUrl(TypedDict):
    url: str


class ImageUrlMessage(TypedDict):
    type: Literal["image_url"]
    image_url: ImageUrl


MessageContent: TypeAlias = TextMessage | ImageUrlMessage


class Message(TypedDict, total=False):
    role: Required[Literal["user", "assistant", "function"]]
    content: Required[list[MessageContent]]
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


async def send(
    *,
    model: OpenAIModel,
    messages: Sequence[Message],
    functions: Sequence[FunctionSchema] | None = None,
) -> ChatCompletion:
    """\
    Send a message to the OpenAI API, as a given model.

    https://platform.openai.com/docs/api-reference/chat
    """
    kwargs: dict[str, Any] = {"model": model.value, "messages": messages}
    if functions is not None:
        kwargs["functions"] = functions

    response = await openai_client.chat.completions.create(**kwargs)
    return response
