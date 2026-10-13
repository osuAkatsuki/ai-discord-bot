import base64
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app import ai
from app import attachments
from app import main
from app import openai_pricing
from app.adapters.openai import gpt
from app.adapters.openai import images
from app.errors import Error
from app.usecases import ai_conversations


def response_body(output, *, status="completed"):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": ai.DEFAULT_AI_MODEL.value,
        "status": status,
        "output": output,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_tokens_details": {"cached_tokens": 50, "cache_write_tokens": 10},
            "output_tokens_details": {"reasoning_tokens": 12},
        },
    }


@pytest.mark.asyncio
async def test_responses_wire_contract_preserves_reasoning_tools_files_and_citations(
    monkeypatch,
):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=response_body(
                [
                    {
                        "type": "web_search_call",
                        "id": "ws_1",
                        "status": "completed",
                        "action": {"type": "search", "query": "test"},
                    },
                    {
                        "type": "web_search_call",
                        "id": "ws_2",
                        "status": "completed",
                        "action": {"type": "open_page", "url": "https://example.com"},
                    },
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Answer SOURCE",
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "start_index": 7,
                                        "end_index": 13,
                                        "url": "https://example.com/a(b)",
                                        "title": "Example",
                                    }
                                ],
                            }
                        ],
                    },
                ]
            ),
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(gpt, "openai_client", client)
        previous = ai.AIResponse(
            None,
            [ai.FunctionCall("weather", "{}", "call_1")],
            1,
            1,
            [
                {
                    "type": "reasoning",
                    "id": "r_1",
                    "summary": [],
                    "encrypted_content": "opaque",
                },
                {
                    "type": "function_call",
                    "name": "weather",
                    "arguments": "{}",
                    "call_id": "call_1",
                },
            ],
        )
        response = await ai.send(
            model=ai.DEFAULT_AI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "file", "file_url": "https://example.com/file.pdf"}
                    ],
                }
            ],
            reasoning_effort="high",
            web_search=True,
            functions=[
                {
                    "name": "weather",
                    "description": "Weather",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                }
            ],
            tool_rounds=[
                ai.ToolRound(
                    previous,
                    [
                        ai.ToolResult(
                            previous.function_calls[0],
                            {"type": "text", "text": "sunny"},
                        )
                    ],
                )
            ],
        )
    request = requests[0]
    assert request["reasoning"] == {"effort": "high"}
    assert request["store"] is False
    assert request["service_tier"] == "default"
    assert request["input"][0]["content"] == [
        {"type": "input_file", "file_url": "https://example.com/file.pdf"}
    ]
    assert request["input"][1:3] == previous.provider_state
    assert request["input"][3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "sunny",
    }
    assert {t["type"] for t in request["tools"]} == {"function", "web_search"}
    assert (
        response.response_content == "Answer SOURCE [1](<https://example.com/a%28b%29>)"
    )
    assert (
        response.output_tokens == 20
    )  # Includes reasoning tokens; don't add them twice.
    assert response.estimated_cost_usd == openai_pricing.response_cost(
        ai.DEFAULT_AI_MODEL, response.usage, 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,effort",
    [(ai.AIModel.OPENAI_GPT_6_ASTRA, "none"), (ai.AIModel.DEEPSEEK_CHAT, None)],
)
async def test_incompatible_options_never_call_provider(monkeypatch, model, effort):
    create = AsyncMock()
    monkeypatch.setattr(gpt.openai_client.responses, "create", create)
    with pytest.raises(ValueError):
        await ai.send(model=model, messages=[], reasoning_effort=effort)
    create.assert_not_awaited()


@pytest.mark.parametrize(
    "status,content,expected",
    [
        (
            "completed",
            [{"type": "refusal", "refusal": "Cannot help with that."}],
            "Cannot help with that.",
        ),
        ("incomplete", [], "Response incomplete"),
        ("completed", [], "returned no text"),
    ],
)
def test_nontext_responses_do_not_crash(status, content, expected):
    from openai.types.responses import Response

    from openai.types.responses import ResponseOutputMessage, ResponseUsage

    body = response_body([], status=status)
    response = Response.model_construct(
        status=status,
        usage=ResponseUsage.model_validate(body["usage"]),
        output=[
            ResponseOutputMessage.model_validate(
                {
                    "type": "message",
                    "id": "msg",
                    "role": "assistant",
                    "status": "completed",
                    "content": content,
                }
            )
        ],
    )
    result = gpt._normalize_responses_response(response, ai.DEFAULT_AI_MODEL)
    assert expected in result.response_content


def test_cost_separates_cached_written_and_output_tokens(monkeypatch):
    model = ai.DEFAULT_AI_MODEL
    monkeypatch.setitem(openai_pricing.TOKEN_RATES, model, (2, 0.5, 3))
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
        "output_tokens_details": {"reasoning_tokens": 15},
    }
    # 50 ordinary + 40 cached + 10 writes; output already includes reasoning.
    assert openai_pricing.response_cost(model, usage, 2) == Decimal("0.020205")
    assert openai_pricing.response_cost(model, None) is None


@pytest.mark.parametrize("input_tokens,long", [(272_000, False), (272_001, True)])
def test_long_context_rates_apply_to_entire_request(monkeypatch, input_tokens, long):
    model = ai.DEFAULT_AI_MODEL
    monkeypatch.setitem(openai_pricing.TOKEN_RATES, model, (2, 0.5, 3))
    usage = {
        "input_tokens": input_tokens,
        "output_tokens": 20,
        "input_tokens_details": {"cached_tokens": 40, "cache_write_tokens": 10},
    }
    input_cost = (
        Decimal(input_tokens - 50) * 2
        + Decimal(40) * Decimal("0.5")
        + Decimal(10) * Decimal("2.5")
    )
    output_cost = Decimal(60)
    if long:
        input_cost *= 2
        output_cost *= Decimal("1.5")
    assert (
        openai_pricing.response_cost(model, usage)
        == (input_cost + output_cost) / 1_000_000
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", [False, True])
async def test_images_use_direct_endpoint_and_return_files_with_usage(
    monkeypatch, edit
):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "created": 1,
                "data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}],
                "usage": {
                    "input_tokens": 30,
                    "input_tokens_details": {"text_tokens": 10, "image_tokens": 20},
                    "output_tokens": 40,
                    "total_tokens": 70,
                },
            },
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(images, "openai_client", client)
        result = await ai.generate_image(
            model=ai.ImageModel.OPENAI_SUNBURST,
            prompt="A cat",
            quality="medium",
            size="1024x1024",
            background="transparent",
            reference_image=(
                ("reference.png", b"reference-bytes", "image/png") if edit else None
            ),
        )
    assert requests[0].url.path == (
        "/v1/images/edits" if edit else "/v1/images/generations"
    )
    assert result.images == [b"image-bytes"]
    assert result.estimated_cost_usd == Decimal("0.00141")
    if edit:
        assert b"reference-bytes" in requests[0].content
    else:
        assert json.loads(requests[0].content)["output_format"] == "webp"


def test_attachments_preserve_signed_urls_and_reject_unsupported_files():
    image = SimpleNamespace(
        filename="photo.WEBP", url="https://cdn.example/photo.WEBP?signature=x", size=10
    )
    pdf = SimpleNamespace(
        filename="document.pdf",
        url="https://cdn.example/document.pdf?signature=x",
        size=10,
    )
    parts = attachments.content_from_attachments([image, pdf])
    assert parts == [
        {"type": "image_url", "image_url": {"url": image.url}},
        {"type": "file", "file_url": pdf.url},
    ]
    with pytest.raises(ValueError):
        attachments.content_from_attachments(
            [SimpleNamespace(filename="video.mp4", url="test", size=10)]
        )
    with pytest.raises(ValueError):
        attachments.content_from_attachments(
            [
                SimpleNamespace(
                    filename="large.pdf",
                    url="test",
                    size=attachments.MAX_ATTACHMENT_BYTES + 1,
                )
            ]
        )


def test_prompt_extraction_preserves_code_and_newlines():
    prompt = (
        "Explain:\n```python\n    print('hello')\n```\nhttps://example.com/a.WEBP?sig=1"
    )
    text, content = ai_conversations._message_content_from_prompt(prompt, [])
    assert "\n    print('hello')\n" in text
    assert content[1]["image_url"]["url"] == "https://example.com/a.WEBP?sig=1"


@pytest.mark.asyncio
async def test_tool_failure_is_returned_to_model_and_all_rounds_are_recorded(
    monkeypatch,
):
    call = ai.FunctionCall("missing_tool", "{}", "call_1")
    responses = [
        ai.AIResponse(None, [call], 10, 2, [], estimated_cost_usd=Decimal("0.01")),
        ai.AIResponse(
            "Tool unavailable", [], 12, 3, [], estimated_cost_usd=Decimal("0.02")
        ),
    ]
    captured = []

    async def send(**kwargs):
        captured.append(list(kwargs["tool_rounds"]))
        return responses.pop(0)

    record = AsyncMock()
    monkeypatch.setattr(ai, "send", send)
    monkeypatch.setattr(ai_conversations.ai_requests, "create", record)
    result = await ai_conversations.make_ai_request(
        [], ai.DEFAULT_AI_MODEL, thread_id=1, requester_id=2
    )
    assert not isinstance(result, Error)
    assert captured[1][0].results[0].call == call
    assert "failed" in captured[1][0].results[0].content["text"]
    assert [c.kwargs["estimated_cost_usd"] for c in record.call_args_list] == [
        Decimal("0.01"),
        Decimal("0.02"),
    ]


def test_discord_commands_serialize_new_options():
    commands = {
        c.name: c.to_dict(main.command_tree) for c in main.command_tree.get_commands()
    }
    assert {p["name"] for p in commands["devquery"]["options"]} >= {
        "attachment",
        "reasoning",
        "web_search",
    }
    assert {p["name"] for p in commands["devimage"]["options"]} >= {
        "reference",
        "quality",
        "background",
    }
    model_options = next(
        p for p in commands["devquery"]["options"] if p["name"] == "model"
    )
    assert all(not c["value"].startswith("deepseek") for c in model_options["choices"])


@pytest.mark.asyncio
@pytest.mark.parametrize("context_length", [0, 1])
async def test_thread_history_preserves_files_and_honors_zero_context(
    monkeypatch, context_length
):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def typing():
        yield

    bot_user = SimpleNamespace(id=999, mention="<@999>")
    bot = SimpleNamespace(user=bot_user)
    message = SimpleNamespace(
        author=SimpleNamespace(id=285190493703503872),
        mentions=[bot_user],
        clean_content="follow up",
        attachments=[],
        channel=SimpleNamespace(id=100, typing=typing),
    )
    parts = [{"type": "file", "file_url": "https://example.com/document.pdf"}]
    monkeypatch.setattr(
        ai_conversations.threads,
        "fetch_one",
        AsyncMock(
            return_value=SimpleNamespace(
                model=ai.DEFAULT_AI_MODEL,
                context_length=context_length,
                reasoning_effort="high",
                web_search=True,
            )
        ),
    )
    monkeypatch.setattr(
        ai_conversations.thread_messages,
        "fetch_many",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    role="user",
                    content="read this",
                    content_parts=parts,
                )
            ]
        ),
    )
    request = AsyncMock(return_value=ai_conversations.AIRequestResponse("done", 10, 5))
    monkeypatch.setattr(ai_conversations, "make_ai_request", request)
    persist = AsyncMock()
    monkeypatch.setattr(ai_conversations.thread_messages, "create", persist)
    result = await ai_conversations.send_message_to_thread(bot, message)
    assert not isinstance(result, Error)
    history = request.call_args.args[0]
    assert len(history) == context_length + 1
    if context_length:
        assert history[0]["content"] == parts
    assert request.call_args.kwargs["reasoning_effort"] == "high"
    assert request.call_args.kwargs["web_search"] is True
    assert persist.call_args_list[0].kwargs["content_parts"] == history[-1]["content"]


@pytest.mark.asyncio
async def test_image_command_checks_authorization_before_reading_attachment(
    monkeypatch,
):
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=0), response=SimpleNamespace(send_message=AsyncMock())
    )
    reference = SimpleNamespace(read=AsyncMock())
    generate = AsyncMock()
    monkeypatch.setattr(ai, "generate_image", generate)
    await main.image.callback(interaction, "cat", reference=reference)
    reference.read.assert_not_awaited()
    generate.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
