from app.adapters.openai import gpt


INITIAL_SETUPS: dict[str, list[gpt.Message]] = {
    "akatsuki-db": [
        {
            "role": "system",
            "content": "Answer user questions by generating SQL queries against the Akatsuki Database.",
        },
        {
            "role": "user",
            "content": "Hi, who are the last 5 users by id?",
        },
        {
            "role": "assistant",
            "content": "The last 5 users by id are testote, Slazerxo, MamkoEb_3000, Constanze and funnyguyastius.",
        },
    ]
}


def get_initial_setup(name: str) -> list[gpt.Message] | None:
    return INITIAL_SETUPS.get(name)
