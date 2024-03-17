# https://openai.com/pricing


def input_price_per_million_tokens(model: str) -> float:
    match model:
        case "gpt-4-0125-preview" | "gpt-4-1106-preview" | "gpt-4-1106-vision-preview":
            return 10.00
        case "gpt-4":
            return 30.00
        case "gpt-4-32k":
            return 60.00
        case "gpt-3.5-turbo-0125":
            return 0.50
        case "gpt-3.5-turbo-instruct":
            return 1.50
        case "gpt-3.5-turbo-1106":
            return 1.00
        case "gpt-3.5-turbo-0613":
            return 1.50
        case "gpt-3.5-turbo-16k-0613":
            return 3.00
        case "gpt-3.5-turbo-0301":
            return 1.50
        case _:
            raise NotImplementedError(f"Unknown model: {model}")


def output_price_per_million_tokens(model: str) -> float:
    match model:
        case "gpt-4-0125-preview" | "gpt-4-1106-preview" | "gpt-4-1106-vision-preview":
            return 30.00
        case "gpt-4":
            return 60.00
        case "gpt-4-32k":
            return 120.00
        case "gpt-3.5-turbo-0125":
            return 1.50
        case "gpt-3.5-turbo-instruct":
            return 2.00
        case "gpt-3.5-turbo-1106":
            return 2.00
        case "gpt-3.5-turbo-0613":
            return 2.00
        case "gpt-3.5-turbo-16k-0613":
            return 4.00
        case "gpt-3.5-turbo-0301":
            return 2.00
        case _:
            raise NotImplementedError(f"Unknown model: {model}")


def tokens_to_dollars(model: str, input_tokens: int, output_tokens: int) -> float:
    per_million_input_price = input_price_per_million_tokens(model)
    per_million_output_price = output_price_per_million_tokens(model)
    return (
        input_tokens * per_million_input_price
        + output_tokens * per_million_output_price
    ) / 1_000_000
