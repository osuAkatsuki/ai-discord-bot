# https://openai.com/pricing
from app.adapters.openai.gpt import OpenAIModel


def input_price_per_million_tokens(model: OpenAIModel) -> float:
    match model:
        case (
            OpenAIModel.GPT_4_TURBO_PREVIEW
            | OpenAIModel.GPT_4_0125_PREVIEW
            | OpenAIModel.GPT_4_1106_PREVIEW
            | OpenAIModel.GPT_4_1106_VISION_PREVIEW
        ):
            return 10.00
        case OpenAIModel.GPT_4:
            return 30.00
        case OpenAIModel.GPT_4_32K:
            return 60.00
        case OpenAIModel.GPT_3_5_TURBO | OpenAIModel.GPT_3_5_TURBO_0125:
            return 0.50
        case OpenAIModel.GPT_3_5_TURBO_INSTRUCT:
            return 1.50
        case OpenAIModel.GPT_3_5_TURBO_1106:
            return 1.00
        case OpenAIModel.GPT_3_5_TURBO_0613:
            return 1.50
        case OpenAIModel.GPT_3_5_TURBO_16K_0613:
            return 3.00
        case OpenAIModel.GPT_3_5_TURBO_0301:
            return 1.50
        case _:
            raise NotImplementedError(f"Unknown model: {model}")


def output_price_per_million_tokens(model: OpenAIModel) -> float:
    match model:
        case (
            OpenAIModel.GPT_4_TURBO_PREVIEW
            | OpenAIModel.GPT_4_0125_PREVIEW
            | OpenAIModel.GPT_4_1106_PREVIEW
            | OpenAIModel.GPT_4_1106_VISION_PREVIEW
        ):
            return 30.00
        case OpenAIModel.GPT_4:
            return 60.00
        case OpenAIModel.GPT_4_32K:
            return 120.00
        case OpenAIModel.GPT_3_5_TURBO | OpenAIModel.GPT_3_5_TURBO_0125:
            return 1.50
        case OpenAIModel.GPT_3_5_TURBO_INSTRUCT:
            return 2.00
        case OpenAIModel.GPT_3_5_TURBO_1106:
            return 2.00
        case OpenAIModel.GPT_3_5_TURBO_0613:
            return 2.00
        case OpenAIModel.GPT_3_5_TURBO_16K_0613:
            return 4.00
        case OpenAIModel.GPT_3_5_TURBO_0301:
            return 2.00
        case _:
            raise NotImplementedError(f"Unknown model: {model}")


def tokens_to_dollars(
    model: OpenAIModel,
    input_tokens: int,
    output_tokens: int,
) -> float:
    per_million_input_price = input_price_per_million_tokens(model)
    per_million_output_price = output_price_per_million_tokens(model)
    return (
        input_tokens * per_million_input_price
        + output_tokens * per_million_output_price
    ) / 1_000_000
