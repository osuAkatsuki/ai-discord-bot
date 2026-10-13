import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

import openai

from app import openai_pricing
from app import settings
from app.ai import AIModel
from app.ai import AIResponse
from app.ai import FunctionCall
from app.ai import FunctionSchema
from app.ai import Message
from app.ai import MessageContent
from app.ai import SELECTABLE_MODELS
from app.ai import ToolRound

openai_client = openai.AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
)


def validate_options(
    model: AIModel, reasoning_effort: str | None, web_search: bool
) -> None:
    if model in {AIModel.DEEPSEEK_CHAT, AIModel.DEEPSEEK_REASONER}:
        raise ValueError(
            "DeepSeek support has ended. Use /model to select an OpenAI model."
        )
    if reasoning_effort is not None:
        if model not in SELECTABLE_MODELS:
            raise ValueError("Select a current model to configure reasoning effort.")
        if reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Unsupported reasoning effort.")
        if reasoning_effort == "none" and model != AIModel.OPENAI_GPT_6_LUNA:
            raise ValueError("Only GPT-6 Luna supports reasoning effort 'none'.")
    if web_search and model not in SELECTABLE_MODELS:
        raise ValueError("Select a current model to enable web search.")


def _message_content_to_text(content: Sequence[MessageContent]) -> str:
    output_parts: list[str] = []
    for content_part in content:
        if content_part["type"] == "text":
            output_parts.append(content_part["text"])
        elif content_part["type"] == "image_url":
            output_parts.append(content_part["image_url"]["url"])
        elif content_part["type"] == "file":
            output_parts.append(content_part["file_url"])
    return "\n".join(output_parts)


def _message_to_responses_input(message: Message) -> dict[str, Any]:
    role = message["role"]
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

        elif content_part["type"] == "file":
            content.append({"type": "input_file", "file_url": content_part["file_url"]})

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


def _normalize_responses_response(response: Any, model: AIModel) -> AIResponse:
    usage = response.usage
    response_items = [item.model_dump(exclude_none=True) for item in response.output]
    function_calls = [
        FunctionCall(
            name=item["name"], arguments=item["arguments"], call_id=item["call_id"]
        )
        for item in response_items
        if item["type"] == "function_call"
    ]
    text_parts: list[str] = []
    sources: dict[str, int] = {}
    for item in response_items:
        if item["type"] != "message":
            continue
        for part in item.get("content", []):
            if part["type"] == "refusal":
                text_parts.append(part["refusal"])
            elif part["type"] == "output_text":
                text = part["text"]
                citations = [
                    a
                    for a in part.get("annotations", [])
                    if a["type"] == "url_citation"
                ]
                for annotation in citations:
                    sources.setdefault(annotation["url"], len(sources) + 1)
                for annotation in sorted(
                    citations, key=lambda a: a["end_index"], reverse=True
                ):
                    url = quote(annotation["url"], safe=":/?#[]@!$&'+,;=%")
                    label = sources[annotation["url"]]
                    text = (
                        text[: annotation["end_index"]]
                        + f" [{label}](<{url}>)"
                        + text[annotation["end_index"] :]
                    )
                text_parts.append(re.sub(r"cite.*?", "", text))
    content = "\n".join(text_parts) or response.output_text or ""
    if response.status == "incomplete":
        content += "\n\n[Response incomplete; try a shorter request or lower reasoning effort.]"
    if not content and not function_calls:
        content = "The model returned no text. Try again or choose a different model."
    usage_data = usage.model_dump() if usage is not None else None
    web_search_calls = sum(
        item["type"] == "web_search_call" and item["action"]["type"] == "search"
        for item in response_items
    )
    if usage_data is not None:
        usage_data["web_search_calls"] = web_search_calls
    return AIResponse(
        response_content=content or None,
        function_calls=function_calls,
        input_tokens=usage.input_tokens if usage is not None else 0,
        output_tokens=usage.output_tokens if usage is not None else 0,
        provider_state=response_items,
        usage=usage_data,
        estimated_cost_usd=openai_pricing.response_cost(
            model, usage_data, web_search_calls
        ),
    )


def function_call_output_item(
    function_call: FunctionCall,
    function_response: MessageContent,
) -> dict[str, Any]:
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
    tool_rounds: Sequence[ToolRound] | None = None,
    reasoning_effort: str | None = None,
    web_search: bool = False,
) -> AIResponse:
    """Send text, image, or file inputs using the OpenAI Responses API."""
    validate_options(model, reasoning_effort, web_search)
    input_items = [_message_to_responses_input(message) for message in messages]
    for tool_round in tool_rounds or []:
        input_items.extend(tool_round.response.provider_state)
        input_items.extend(
            function_call_output_item(result.call, result.content)
            for result in tool_round.results
        )
    kwargs: dict[str, Any] = {
        "model": model.value,
        "input": input_items,
        "store": False,
        "service_tier": "default",
        "include": ["reasoning.encrypted_content"],
    }
    if model in SELECTABLE_MODELS:
        kwargs["reasoning"] = {"effort": reasoning_effort or "low"}
    tools = [
        _function_schema_to_responses_tool(function) for function in functions or []
    ]
    if web_search:
        tools.append({"type": "web_search"})
        kwargs["max_tool_calls"] = 5
    if tools:
        kwargs["tools"] = tools
    return _normalize_responses_response(
        await openai_client.responses.create(**kwargs), model
    )
