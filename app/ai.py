# Provider-neutral conversation contracts and the active provider entry point.
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from typing import Literal
from typing import Required
from typing import TypeAlias
from typing import TypedDict


class AIModel(StrEnum):
    # OpenAI
    OPENAI_GPT_6_ASTRA = "gpt-6-astra"
    OPENAI_GPT_6_1_SOL = "gpt-6.1-sol"
    OPENAI_GPT_6_LUNA = "gpt-6-luna"
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

    # Retained only to deserialize historical threads; no provider requests.
    DEEPSEEK_CHAT = "deepseek-chat"
    DEEPSEEK_REASONER = "deepseek-reasoner"


DEFAULT_AI_MODEL = AIModel.OPENAI_GPT_6_1_SOL
SELECTABLE_MODELS = (
    AIModel.OPENAI_GPT_6_1_SOL,
    AIModel.OPENAI_GPT_6_ASTRA,
    AIModel.OPENAI_GPT_6_LUNA,
)
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]


class TextMessage(TypedDict):
    type: Literal["text"]
    text: str


class ImageUrl(TypedDict):
    url: str


class ImageUrlMessage(TypedDict):
    type: Literal["image_url"]
    image_url: ImageUrl


class FileMessage(TypedDict):
    type: Literal["file"]
    file_url: str


MessageContent: TypeAlias = TextMessage | ImageUrlMessage | FileMessage


class Message(TypedDict):
    role: Literal["user", "assistant"]
    content: list[MessageContent]


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
    call_id: str


@dataclass(frozen=True, slots=True)
class ExecutionSession:
    id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionFile:
    session_id: str
    id: str
    filename: str


@dataclass(frozen=True, slots=True)
class AIResponse:
    response_content: str | None
    function_calls: list[FunctionCall]
    input_tokens: int
    output_tokens: int
    # Opaque provider continuation; interpreted only by the matching adapter.
    provider_state: list[dict[str, Any]]
    usage: dict[str, Any] | None = None
    estimated_cost_usd: Decimal | None = None
    files: tuple[ExecutionFile, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolResult:
    call: FunctionCall
    content: MessageContent


@dataclass(frozen=True, slots=True)
class ToolRound:
    response: AIResponse
    results: list[ToolResult]


async def send(
    *,
    model: AIModel,
    messages: Sequence[Message],
    functions: Sequence[FunctionSchema] | None = None,
    tool_rounds: Sequence[ToolRound] | None = None,
    reasoning_effort: str | None = None,
    web_search: bool = False,
    execution_session_id: str | None = None,
) -> AIResponse:
    from app.adapters.openai import gpt

    return await gpt.send(
        model=model,
        messages=messages,
        functions=functions,
        tool_rounds=tool_rounds,
        reasoning_effort=reasoning_effort,
        web_search=web_search,
        execution_session_id=execution_session_id,
    )


def validate_options(
    model: AIModel, reasoning_effort: str | None, web_search: bool
) -> None:
    from app.adapters.openai import gpt

    gpt.validate_options(model, reasoning_effort, web_search)


class ImageModel(StrEnum):
    OPENAI_FLARE = "gpt-image-2.5-flare"
    OPENAI_SUNBURST = "gpt-image-2.5-sunburst"


@dataclass(frozen=True, slots=True)
class ImageResponse:
    images: list[bytes]
    usage: dict[str, Any] | None
    estimated_cost_usd: Decimal | None


async def generate_image(
    *,
    model: ImageModel,
    prompt: str,
    quality: str,
    size: str,
    background: str,
    reference_image: tuple[str, bytes, str] | None = None,
) -> ImageResponse:
    from app.adapters.openai import images

    return await images.generate(
        model=model,
        prompt=prompt,
        quality=quality,
        size=size,
        background=background,
        reference_image=reference_image,
    )


def validate_execution(model: AIModel) -> None:
    from app.adapters.openai import execution

    execution.validate_model(model)


async def create_execution_session() -> ExecutionSession:
    from app.adapters.openai import execution

    return await execution.create_session()


async def get_execution_session(session_id: str) -> ExecutionSession | None:
    from app.adapters.openai import execution

    return await execution.get_session(session_id)


async def delete_execution_session(session_id: str) -> None:
    from app.adapters.openai import execution

    await execution.delete_session(session_id)


async def upload_execution_file(session_id: str, filename: str, content: bytes) -> str:
    from app.adapters.openai import execution

    return await execution.upload_file(session_id, filename, content)


async def download_execution_file(file: ExecutionFile, max_bytes: int) -> bytes:
    from app.adapters.openai import execution

    return await execution.download_file(file, max_bytes)


def execution_session_cost(session: ExecutionSession) -> Decimal:
    from app.adapters.openai import execution

    return execution.session_cost(session)
