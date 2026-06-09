import re
from typing import Any

import pytest

from app import openai_functions
from app.adapters.openai import gpt
from app.usecases import ai_conversations


def test_get_author_name_uses_stable_pseudonym():
    author_name = ai_conversations.get_author_name(285190493703503872)

    assert re.fullmatch(r"User #[0-9a-f]{8}", author_name)
    assert author_name == ai_conversations.get_author_name(285190493703503872)
    assert author_name != ai_conversations.get_author_name(285190493703503873)


def test_message_content_from_prompt_includes_urls_and_attachments():
    prompt, content = ai_conversations._message_content_from_prompt(
        "User #123: can you read https://example.com/code.png",
        ["https://cdn.discordapp.com/attachment.jpg"],
    )

    assert prompt == "User #123: can you read {IMAGE #1}"
    assert content == [
        {
            "type": "text",
            "text": "User #123: can you read {IMAGE #1}",
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/code.png"},
        },
        {
            "type": "image_url",
            "image_url": {"url": "https://cdn.discordapp.com/attachment.jpg"},
        },
    ]


@pytest.mark.asyncio
async def test_make_gpt_request_continues_responses_function_call(
    monkeypatch,
):
    responses = [
        gpt.AIResponse(
            response_content=None,
            function_calls=[
                gpt.FunctionCall(
                    name="get_weather_for_location",
                    arguments='{"location": "Tokyo"}',
                    call_id="call_123",
                )
            ],
            input_tokens=10,
            output_tokens=2,
            response_items=[
                {
                    "type": "function_call",
                    "name": "get_weather_for_location",
                    "arguments": '{"location": "Tokyo"}',
                    "call_id": "call_123",
                }
            ],
        ),
        gpt.AIResponse(
            response_content="Tokyo is 22C.",
            function_calls=[],
            input_tokens=12,
            output_tokens=4,
            response_items=[],
        ),
    ]
    captured_context_items: list[list[dict[str, Any]]] = []

    async def fake_send(
        *,
        model: gpt.AIModel,
        messages: list[gpt.Message],
        functions: list[gpt.FunctionSchema],
        response_context_items: list[dict[str, Any]],
    ) -> gpt.AIResponse:
        captured_context_items.append(list(response_context_items))
        return responses.pop(0)

    async def fake_weather_callback(location: str) -> gpt.MessageContent:
        return {"type": "text", "text": f"{location} is 22C."}

    monkeypatch.setattr(gpt, "send", fake_send)
    monkeypatch.setattr(
        openai_functions,
        "get_full_openai_functions_schema",
        lambda: [],
    )
    monkeypatch.setitem(
        openai_functions.ai_functions,
        "get_weather_for_location",
        {"callback": fake_weather_callback, "schema": {}},
    )

    result = await ai_conversations._make_gpt_request(
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "weather in Tokyo?"}],
            }
        ],
        gpt.AIModel.OPENAI_GPT_5_4,
    )

    assert result == ai_conversations._GptRequestResponse(
        response_content="Tokyo is 22C.",
        input_tokens=22,
        output_tokens=6,
    )
    assert captured_context_items[0] == []
    assert captured_context_items[1] == [
        {
            "type": "function_call",
            "name": "get_weather_for_location",
            "arguments": '{"location": "Tokyo"}',
            "call_id": "call_123",
        },
        {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "Tokyo is 22C.",
        },
    ]
