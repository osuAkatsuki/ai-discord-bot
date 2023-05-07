def price_for_model(model: str) -> float:
    # https://openai.com/pricing
    match model:
        case "gpt-4":
            return 0.03
        # case "gpt-4-8k":
        #     return 0.03
        # case "gpt-4-32k":
        #     return 0.06
        case "gpt-3.5-turbo":
            return 0.02
        case "davinci":
            return 0.02
        case _:
            raise NotImplementedError(f"Unknown model: {model}")


def tokens_to_dollars(model: str, tokens: int) -> float:
    # https://openai.com/pricing
    dollars_per_1k_tokens = price_for_model(model)
    return tokens * (dollars_per_1k_tokens / 1000)
