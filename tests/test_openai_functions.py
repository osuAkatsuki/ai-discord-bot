from typing import Any

import pytest

from app import openai_functions
from app import state


class _FakeResponse:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.data


class _FakeHttpClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, params: dict[str, Any]) -> _FakeResponse:
        self.requests.append((url, params))
        if "geocode" in url:
            return _FakeResponse(
                {
                    "results": [
                        {
                            "geometry": {
                                "location": {
                                    "lat": 35.6764,
                                    "lng": 139.65,
                                },
                            },
                        },
                    ],
                }
            )

        return _FakeResponse({"current": {"temperature_2m": 22.3}})


@pytest.mark.asyncio
async def test_weather_function_uses_current_temperature(monkeypatch):
    http_client = _FakeHttpClient()
    monkeypatch.setattr(state, "http_client", http_client, raising=False)
    monkeypatch.setattr(openai_functions, "location_cache", {})

    response = await openai_functions.get_weather_for_location("Tokyo")

    assert response == {
        "type": "text",
        "text": "22.3°C / 72.14°F",
    }
    assert http_client.requests[1] == (
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": 35.6764,
            "longitude": 139.65,
            "current": "temperature_2m",
        },
    )
