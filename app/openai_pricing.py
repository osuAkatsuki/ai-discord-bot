# https://openai.com/pricing
from app.adapters.openai.gpt import OpenAIModel
from app.adapters.openai.gpt import DeepSeekAIModel
from app.adapters.openai.gpt import AIModel


def input_price_per_million_tokens(model: AIModel) -> float:
    match model:
        case OpenAIModel.GPT_4_OMNI:
            return 2.50
        # Not listed on the pricing page
        case OpenAIModel.CHATGPT_4O_LATEST:
            return 2.50
        case OpenAIModel.GPT_O1:
            return 15.00
        case OpenAIModel.GPT_O1_MINI:
            return 1.10
        case OpenAIModel.GPT_O3_MINI:
            return 1.10
        case DeepSeekAIModel.DEEPSEEK_CHAT:
            return 0.27
        case DeepSeekAIModel.DEEPSEEK_REASONER:
            return 0.55
        case _:
            raise NotImplementedError(f"Unknown model: {model}")


def output_price_per_million_tokens(model: AIModel) -> float:
    match model:
        case OpenAIModel.GPT_4_OMNI:
            return 10.00
        # Not listed on the pricing page
        case OpenAIModel.CHATGPT_4O_LATEST:
            return 10.00
        case OpenAIModel.GPT_O1:
            return 60.00
        case OpenAIModel.GPT_O1_MINI:
            return 4.40
        case OpenAIModel.GPT_O3_MINI:
            return 4.40
        case DeepSeekAIModel.DEEPSEEK_CHAT:
            return 1.10
        case DeepSeekAIModel.DEEPSEEK_REASONER:
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
