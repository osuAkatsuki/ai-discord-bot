from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from typing import Literal
from typing import Required
from typing import TypeAlias
from typing import TypedDict

import openai

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
    OPENAI_GPT_5_5 = "gpt-5.5"
    OPENAI_GPT_5_4 = "gpt-5.4"
    OPENAI_GPT_5_4_MINI = "gpt-5.4-mini"
    OPENAI_GPT_5_4_NANO = "gpt-5.4-nano"
    OPENAI_GPT_5 = "gpt-5"
    OPENAI_GPT_5_MINI = "gpt-5-mini"
    OPENAI_GPT_4_OMNI = "gpt-4o"
    OPENAI_GPT_O3 = "o3"
    OPENAI_GPT_O3_PRO = "o3-pro"
    OPENAI_GPT_O4_MINI = "o4-mini"

    # DeepSeek
    DEEPSEEK_CHAT = "deepseek-chat"
    DEEPSEEK_REASONER = "deepseek-reasoner"


DEFAULT_AI_MODEL = AIModel.OPENAI_GPT_5_4


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
    call_id: str  # only exists for Responses API function outputs


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


@dataclass(frozen=True, slots=True)
class FunctionCall:
    name: str
    arguments: str
    call_id: str | None = None


@dataclass(frozen=True, slots=True)
class AIResponse:
    response_content: str | None
    function_calls: list[FunctionCall]
    input_tokens: int
    output_tokens: int
    response_items: list[dict[str, Any]]


def _message_content_to_text(content: Sequence[MessageContent]) -> str:
    output_parts: list[str] = []
    for content_part in content:
        if content_part["type"] == "text":
            output_parts.append(content_part["text"])
        elif content_part["type"] == "image_url":
            output_parts.append(content_part["image_url"]["url"])
    return "\n".join(output_parts)


def _message_to_responses_input(message: Message) -> dict[str, Any]:
    role = message["role"]
    if role == "function":
        return {
            "type": "function_call_output",
            "call_id": message["call_id"],
            "output": _message_content_to_text(message["content"]),
        }

    if role == "assistant":
        return {
            "role": "assistant",
            "content": _message_content_to_text(message["content"]),
        }

    content: list[dict[str, Any]] = []
    for content_part in message["content"]:
        if content_part["type"] == "text":
            content.append(
                {
                    "type": "input_text",
                    "text": content_part["text"],
                }
            )
        elif content_part["type"] == "image_url":
            content.append(
                {
                    "type": "input_image",
                    "image_url": content_part["image_url"]["url"],
                    "detail": "auto",
                }
            )

    return {
        "role": role,
        "content": content,
    }


def _function_schema_to_responses_tool(schema: FunctionSchema) -> dict[str, Any]:
    return {
        "type": "function",
        "name": schema["name"],
        "description": schema["description"],
        "parameters": schema["parameters"],
        "strict": False,
    }


def _normalize_responses_response(response: Any) -> AIResponse:
    usage = response.usage
    response_items = [item.model_dump(exclude_none=True) for item in response.output]
    function_calls = [
        FunctionCall(
            name=item.name,
            arguments=item.arguments,
            call_id=item.call_id,
        )
        for item in response.output
        if item.type == "function_call"
    ]

    return AIResponse(
        response_content=response.output_text or None,
        function_calls=function_calls,
        input_tokens=usage.input_tokens if usage is not None else 0,
        output_tokens=usage.output_tokens if usage is not None else 0,
        response_items=response_items,
    )


def _normalize_chat_response(response: Any) -> AIResponse:
    choice = response.choices[0]
    message = choice.message
    function_call = message.function_call
    function_calls = []
    if choice.finish_reason == "function_call" and function_call is not None:
        function_calls.append(
            FunctionCall(
                name=function_call.name,
                arguments=function_call.arguments,
            )
        )

    usage = response.usage

    return AIResponse(
        response_content=message.content,
        function_calls=function_calls,
        input_tokens=usage.prompt_tokens if usage is not None else 0,
        output_tokens=usage.completion_tokens if usage is not None else 0,
        response_items=[],
    )


def function_call_output_item(
    function_call: FunctionCall,
    function_response: MessageContent,
) -> dict[str, Any]:
    if function_call.call_id is None:
        raise ValueError("OpenAI Responses function calls must include a call_id")

    return {
        "type": "function_call_output",
        "call_id": function_call.call_id,
        "output": _message_content_to_text([function_response]),
    }


async def send(
    *,
    model: AIModel,
    messages: Sequence[Message],
    functions: Sequence[FunctionSchema] | None = None,
    response_context_items: Sequence[dict[str, Any]] | None = None,
) -> AIResponse:
    """\
    Send a message to the selected AI provider, as a given model.

    OpenAI models use the Responses API. DeepSeek uses its OpenAI-compatible
    chat completions endpoint.
    """
    if model in {AIModel.DEEPSEEK_CHAT, AIModel.DEEPSEEK_REASONER}:
        kwargs: dict[str, Any] = {"model": model.value, "messages": messages}
        if functions is not None:
            kwargs["functions"] = functions
        return _normalize_chat_response(
            await deepseek_client.chat.completions.create(**kwargs)
        )

    input_items = [_message_to_responses_input(message) for message in messages]
    if response_context_items is not None:
        input_items.extend(response_context_items)

    kwargs = {
        "model": model.value,
        "input": input_items,
        "store": False,
    }
    if functions is not None:
        kwargs["tools"] = [
            _function_schema_to_responses_tool(function) for function in functions
        ]

    return _normalize_responses_response(await openai_client.responses.create(**kwargs))
