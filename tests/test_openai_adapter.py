from dataclasses import dataclass
from dataclasses import field
from typing import Any

import pytest

from app.adapters.openai import gpt


@dataclass
class _FakeUsage:
    input_tokens: int = 11
    output_tokens: int = 7


class _FakeOutputItem:
    type = "message"

    def model_dump(self, *, exclude_none: bool) -> dict[str, str]:
        return {"type": self.type}


@dataclass
class _FakeResponse:
    output_text: str = "done"
    usage: _FakeUsage = field(default_factory=_FakeUsage)
    output: list[_FakeOutputItem] = field(default_factory=lambda: [_FakeOutputItem()])


@pytest.mark.asyncio
async def test_openai_send_uses_responses_api_with_image_content(monkeypatch):
    captured_kwargs: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> _FakeResponse:
        captured_kwargs.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(gpt.openai_client.responses, "create", fake_create)

    response = await gpt.send(
        model=gpt.AIModel.OPENAI_GPT_5_4,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "can you read this?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/code.png"},
                    },
                ],
            }
        ],
    )

    assert captured_kwargs["model"] == "gpt-5.4"
    assert captured_kwargs["store"] is False
    assert captured_kwargs["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "can you read this?"},
                {
                    "type": "input_image",
                    "image_url": "https://example.com/code.png",
                    "detail": "auto",
                },
            ],
        }
    ]
    assert response.response_content == "done"
    assert response.input_tokens == 11
    assert response.output_tokens == 7


def test_function_call_output_item_uses_responses_call_id():
    function_call = gpt.FunctionCall(
        name="get_weather_for_location",
        arguments='{"location": "Tokyo"}',
        call_id="call_123",
    )

    assert gpt.function_call_output_item(
        function_call,
        {"type": "text", "text": "22C"},
    ) == {
        "type": "function_call_output",
        "call_id": "call_123",
        "output": "22C",
    }
