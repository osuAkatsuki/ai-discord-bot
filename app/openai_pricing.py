# TODO: see if we can fetch these prices on the fly from
# the respective APIs, as they may change frequently, and
# without notice.
# - https://openai.com/api/pricing/
# - https://api-docs.deepseek.com/quick_start/pricing/
from app.adapters.openai.gpt import AIModel


def input_price_per_million_tokens(model: AIModel) -> float:
    match model:
        case AIModel.OPENAI_GPT_4_OMNI:
            return 2.50
        case AIModel.OPENAI_GPT_O3:
            return 2.00
        case AIModel.OPENAI_GPT_O3_PRO:
            return 20.00
        case AIModel.OPENAI_GPT_O4_MINI:
            return 1.10
        case AIModel.DEEPSEEK_CHAT:
            return 0.27
        case AIModel.DEEPSEEK_REASONER:
            return 0.55
        case _:
            raise NotImplementedError(f"Unknown model: {model}")


def output_price_per_million_tokens(model: AIModel) -> float:
    match model:
        case AIModel.OPENAI_GPT_4_OMNI:
            return 10.00
        case AIModel.OPENAI_GPT_O3:
            return 8.00
        case AIModel.OPENAI_GPT_O3_PRO:
            return 80.00
        case AIModel.OPENAI_GPT_O4_MINI:
            return 4.40
        case AIModel.DEEPSEEK_CHAT:
            return 1.10
        case AIModel.DEEPSEEK_REASONER:
            return 2.19
        case _:
            raise NotImplementedError(f"Unknown model: {model}")


def tokens_to_dollars(
    model: AIModel,
    input_tokens: int,
    output_tokens: int,
) -> float:
    per_million_input_price = input_price_per_million_tokens(model)
    per_million_output_price = output_price_per_million_tokens(model)
    return (
        input_tokens * per_million_input_price
        + output_tokens * per_million_output_price
    ) / 1_000_000
