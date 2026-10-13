import re
from types import SimpleNamespace
from typing import Any

import pytest

from app import ai
from app import openai_functions
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
async def test_make_ai_request_continues_responses_function_call(
    monkeypatch,
):
    responses = [
        ai.AIResponse(
            response_content=None,
            function_calls=[
                ai.FunctionCall(
                    name="get_weather_for_location",
                    arguments='{"location": "Tokyo"}',
                    call_id="call_123",
                )
            ],
            input_tokens=10,
            output_tokens=2,
            provider_state=[
                {
                    "type": "function_call",
                    "name": "get_weather_for_location",
                    "arguments": '{"location": "Tokyo"}',
                    "call_id": "call_123",
                }
            ],
        ),
        ai.AIResponse(
            response_content="Tokyo is 22C.",
            function_calls=[],
            input_tokens=12,
            output_tokens=4,
            provider_state=[],
        ),
    ]
    captured_rounds: list[list[ai.ToolRound]] = []
    recorded_requests = []

    async def record_request(**kwargs):
        recorded_requests.append(kwargs)

    monkeypatch.setattr(ai_conversations.ai_requests, "create", record_request)

    async def fake_send(
        *,
        model: ai.AIModel,
        messages: list[ai.Message],
        functions: list[ai.FunctionSchema],
        tool_rounds: list[ai.ToolRound],
        reasoning_effort: str | None,
        web_search: bool,
    ) -> ai.AIResponse:
        captured_rounds.append(list(tool_rounds))
        return responses.pop(0)

    async def fake_weather_callback(location: str) -> ai.MessageContent:
        return {"type": "text", "text": f"{location} is 22C."}

    monkeypatch.setattr(ai, "send", fake_send)
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

    result = await ai_conversations.make_ai_request(
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "weather in Tokyo?"}],
            }
        ],
        ai.AIModel.OPENAI_GPT_5_4,
        thread_id=123,
        requester_id=456,
    )

    assert result == ai_conversations.AIRequestResponse(
        response_content="Tokyo is 22C.",
        input_tokens=22,
        output_tokens=6,
    )
    assert captured_rounds[0] == []
    result_round = captured_rounds[1][0]
    assert result_round.results == [
        ai.ToolResult(
            call=ai.FunctionCall(
                name="get_weather_for_location",
                arguments='{"location": "Tokyo"}',
                call_id="call_123",
            ),
            content={"type": "text", "text": "Tokyo is 22C."},
        )
    ]
    assert len(recorded_requests) == 2
    assert all(
        r["thread_id"] == 123 and r["requester_id"] == 456 for r in recorded_requests
    )


@pytest.mark.asyncio
async def test_send_message_without_context_tracks_query_requester_cost(
    monkeypatch,
):
    created_threads: list[tuple[int, int, ai.AIModel, int]] = []
    created_messages: list[dict[str, Any]] = []

    async def fake_make_ai_request(
        message_history: list[ai.Message],
        model: ai.AIModel,
        **kwargs,
    ) -> ai_conversations.AIRequestResponse:
        assert kwargs["requester_id"] == 285190493703503872
        assert kwargs["thread_id"] == 12345
        assert message_history == [
            {
                "role": "user",
                "content": [{"type": "text", "text": "what changed?"}],
            }
        ]
        assert model == ai.AIModel.OPENAI_GPT_5_4
        return ai_conversations.AIRequestResponse(
            response_content="nothing notable",
            input_tokens=31,
            output_tokens=7,
        )

    async def fake_threads_create(
        thread_id: int,
        initiator_user_id: int,
        model: ai.AIModel,
        context_length: int,
    ) -> None:
        created_threads.append((thread_id, initiator_user_id, model, context_length))

    async def fake_thread_messages_create(
        thread_id: int,
        content: str,
        discord_user_id: int,
        role: str,
        tokens_used: int,
        content_parts=None,
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

    monkeypatch.setattr(ai_conversations, "make_ai_request", fake_make_ai_request)
    monkeypatch.setattr(ai_conversations.threads, "create", fake_threads_create)
    monkeypatch.setattr(
        ai_conversations.thread_messages,
        "create",
        fake_thread_messages_create,
    )

    bot = SimpleNamespace(user=SimpleNamespace(id=999))
    interaction = SimpleNamespace(
        id=12345,
        channel_id=None,
        user=SimpleNamespace(id=285190493703503872),
    )

    result = await ai_conversations.send_message_without_context(
        bot,
        interaction,
        "what changed?",
        ai.AIModel.OPENAI_GPT_5_4,
    )

    assert result == ai_conversations.SendAndReceiveResponse(
        response_messages=["nothing notable"]
    )
    assert created_threads == [
        (
            12345,
            285190493703503872,
            ai.AIModel.OPENAI_GPT_5_4,
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
