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

VALID_IMAGE_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg", ".gif"}

openai_client = openai.AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
)
deepseek_client = openai.AsyncOpenAI(
    api_key=settings.DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com/v1",
)


class AIModel(StrEnum):
    # OpenAI
    OPENAI_GPT_4_OMNI = "gpt-4o"
    OPENAI_GPT_O3 = "o3"
    OPENAI_GPT_O3_PRO = "o3-pro"
    OPENAI_GPT_O4_MINI = "o4-mini"
    OPENAI_GPT_5 = "gpt-5"
    OPENAI_GPT_5_MINI = "gpt-5-mini"

    # DeepSeek
    DEEPSEEK_CHAT = "deepseek-chat"
    DEEPSEEK_REASONER = "deepseek-reasoner"


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
    model: AIModel,
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

    if model in {AIModel.DEEPSEEK_CHAT, AIModel.DEEPSEEK_REASONER}:
        return await deepseek_client.chat.completions.create(**kwargs)
    elif model in {
        AIModel.OPENAI_GPT_4_OMNI,
        AIModel.OPENAI_GPT_O3,
        AIModel.OPENAI_GPT_O3_PRO,
        AIModel.OPENAI_GPT_O4_MINI,
    }:
        return await openai_client.chat.completions.create(**kwargs)
    else:
        raise NotImplementedError(f"Unsupported model: {model}")
