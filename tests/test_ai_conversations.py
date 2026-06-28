import re
from types import SimpleNamespace
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


@pytest.mark.asyncio
async def test_send_message_without_context_tracks_query_requester_cost(
    monkeypatch,
):
    created_threads: list[tuple[int, int, gpt.AIModel, int]] = []
    created_messages: list[dict[str, Any]] = []

    async def fake_make_gpt_request(
        message_history: list[gpt.Message],
        model: gpt.AIModel,
    ) -> ai_conversations._GptRequestResponse:
        assert message_history == [
            {
                "role": "user",
                "content": [{"type": "text", "text": "what changed?"}],
            }
        ]
        assert model == gpt.AIModel.OPENAI_GPT_5_4
        return ai_conversations._GptRequestResponse(
            response_content="nothing notable",
            input_tokens=31,
            output_tokens=7,
        )

    async def fake_threads_create(
        thread_id: int,
        initiator_user_id: int,
        model: gpt.AIModel,
        context_length: int,
    ) -> None:
        created_threads.append((thread_id, initiator_user_id, model, context_length))

    async def fake_thread_messages_create(
        thread_id: int,
        content: str,
        discord_user_id: int,
        role: str,
        tokens_used: int,
    ) -> None:
        created_messages.append(
            {
                "thread_id": thread_id,
                "content": content,
                "discord_user_id": discord_user_id,
                "role": role,
                "tokens_used": tokens_used,
            }
        )

    monkeypatch.setattr(ai_conversations, "_make_gpt_request", fake_make_gpt_request)
    monkeypatch.setattr(ai_conversations.threads, "create", fake_threads_create)
    monkeypatch.setattr(
        ai_conversations.thread_messages,
        "create",
        fake_thread_messages_create,
    )

    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    interaction = SimpleNamespace(
        id=12345,
        user=SimpleNamespace(id=285190493703503872),
    )

    result = await ai_conversations.send_message_without_context(
        bot,
        interaction,
        "what changed?",
        gpt.AIModel.OPENAI_GPT_5_4,
    )

    assert result == ai_conversations.SendAndReceiveResponse(
        response_messages=["nothing notable"]
    )
    assert created_threads == [
        (
            12345,
            285190493703503872,
            gpt.AIModel.OPENAI_GPT_5_4,
            0,
        )
    ]
    assert created_messages == [
        {
            "thread_id": 12345,
            "content": "what changed?",
            "discord_user_id": 285190493703503872,
            "role": "user",
            "tokens_used": 31,
        },
        {
            "thread_id": 12345,
            "content": "nothing notable",
            "discord_user_id": 999,
            "role": "assistant",
            "tokens_used": 7,
        },
    ]
