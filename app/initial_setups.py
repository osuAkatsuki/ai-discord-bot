from typing import Literal
from typing import TypedDict


class InitialSetup(TypedDict):
    content: str
    role: Literal["user", "assistant", "system"]  # TODO: function calling support?


INITIAL_SETUPS: dict[str, list[InitialSetup]] = {
    "akatsuki-db": [
        {
            "content": "Answer user questions by generating SQL queries against the Akatsuki Database.",
            "role": "system",
        },
        {
            "content": "cmyui: Hi, who are the last 5 users by id?",
            "role": "user",
        },
        {
            "content": "The last 5 users by id are testote, Slazerxo, MamkoEb_3000, Constanze and funnyguyastius.",
            "role": "assistant",
        },
    ]
}


def get_initial_setup(name: str) -> list[InitialSetup] | None:
    return INITIAL_SETUPS.get(name)
