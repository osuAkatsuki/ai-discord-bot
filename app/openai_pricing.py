# Standard OpenAI USD rates per million tokens, verified 2026-10-13.
#
# Source: https://developers.openai.com/api/docs/pricing
# Regional processing, Batch, Flex, Fast and Ultrafast are not used by this bot.
from decimal import Decimal
from typing import Any

from app.ai import AIModel

# Input, cached input, output. Historical OpenAI threads remain usable.
TOKEN_RATES = {
    AIModel.OPENAI_GPT_6_ASTRA: (10, 1, 50),
    AIModel.OPENAI_GPT_6_1_SOL: (2, 0.10, 10),
    AIModel.OPENAI_GPT_6_LUNA: (0.10, 0.01, 0.50),
    AIModel.OPENAI_GPT_5_5: (5, 0.50, 30),
    AIModel.OPENAI_GPT_5_4: (2.50, 0.25, 15),
    AIModel.OPENAI_GPT_5_4_MINI: (0.75, 0.075, 4.50),
    AIModel.OPENAI_GPT_5_4_NANO: (0.20, 0.02, 1.25),
    AIModel.OPENAI_GPT_5: (1.25, 0.125, 10),
    AIModel.OPENAI_GPT_5_MINI: (0.25, 0.025, 2),
    AIModel.OPENAI_GPT_4_OMNI: (2.50, 1.25, 10),
    AIModel.OPENAI_GPT_O3: (2, 0.50, 8),
    AIModel.OPENAI_GPT_O3_PRO: (20, 20, 80),
    AIModel.OPENAI_GPT_O4_MINI: (1.10, 0.275, 4.40),
}
CACHE_WRITE_MODELS = {
    AIModel.OPENAI_GPT_6_ASTRA,
    AIModel.OPENAI_GPT_6_1_SOL,
    AIModel.OPENAI_GPT_6_LUNA,
}
LONG_CONTEXT_MODELS = CACHE_WRITE_MODELS | {
    AIModel.OPENAI_GPT_5_5,
    AIModel.OPENAI_GPT_5_4,
}


def input_price_per_million_tokens(model: AIModel) -> float:
    return TOKEN_RATES[model][0]


def output_price_per_million_tokens(model: AIModel) -> float:
    return TOKEN_RATES[model][2]


def response_cost(
    model: AIModel, usage: dict[str, Any] | None, web_search_calls: int = 0
) -> Decimal | None:
    if usage is None:
        return None
    input_rate, cached_rate, output_rate = map(
        lambda x: Decimal(str(x)), TOKEN_RATES[model]
    )
    input_tokens = usage["input_tokens"]
    details = usage.get("input_tokens_details") or {}
    cached = details.get("cached_tokens", 0)
    written = details.get("cache_write_tokens", 0)
    write_rate = input_rate * (Decimal("1.25") if model in CACHE_WRITE_MODELS else 1)
    if model in LONG_CONTEXT_MODELS and input_tokens > 272_000:
        input_rate *= 2
        cached_rate *= 2
        write_rate *= 2
        output_rate *= Decimal("1.5")
    return (
        (input_tokens - cached - written) * input_rate
        + cached * cached_rate
        + written * write_rate
        + usage["output_tokens"] * output_rate
    ) / 1_000_000 + Decimal("0.01") * web_search_calls


def image_cost(usage: dict[str, Any] | None) -> Decimal | None:
    """GPT Image 2.5 direct Images API; this endpoint has no cache discount."""
    if usage is None:
        return None
    details = usage["input_tokens_details"]
    return (
        Decimal(5) * details["text_tokens"]
        + Decimal(8) * details["image_tokens"]
        + Decimal(30) * usage["output_tokens"]
    ) / 1_000_000
