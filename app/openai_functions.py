import itertools
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Annotated
from typing import get_type_hints
from typing import Required
from typing import TypedDict


class Property(TypedDict, total=False):
    type: Required[str]
    enum: list[str]  # optional
    description: Required[str]


class Parameters(TypedDict):
    type: str
    properties: dict[str, Property]
    required: list[str]


class FunctionSchema(TypedDict):
    name: str
    description: str
    parameters: Parameters


class OpenAIFunction(TypedDict):
    callback: Callable[..., Awaitable[str]]
    schema: FunctionSchema


ai_functions: dict[str, OpenAIFunction] = {}

UNSET = object()


def get_function_openai_schema(f: Callable[..., Awaitable[str]]) -> FunctionSchema:
    assert f.__doc__ is not None, "All AI functions must have docstrings"

    schema: FunctionSchema = {
        "name": f.__name__,
        "description": f.__doc__,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }

    hints = get_type_hints(f, include_extras=True)
    for (param_name, param_type), default_val in itertools.zip_longest(  # type: ignore
        hints.items(),
        f.__defaults__ or [],
        fillvalue=UNSET,
    ):
        if param_name == "return":
            continue

        assert (
            len(param_type.__metadata__) == 1
        ), "All AI function parameters must have a description"

        schema["parameters"]["properties"][param_name] = {
            "type": param_type.__origin__.__name__,
            "description": param_type.__metadata__[0],
        }
        if default_val is UNSET:
            schema["parameters"]["required"].append(param_name)

    return schema


def ai_function(f: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    ai_functions[f.__name__] = {
        "callback": f,
        "schema": get_function_openai_schema(f),
    }

    return f


def get_openai_function_schema() -> list[FunctionSchema]:
    return [f["schema"] for f in ai_functions.values()]
    # return [
    #     {
    #         "name": "get_weather_for_location",
    #         "description": "Fetch the weather for a given location.",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "location": {
    #                     "type": "string",
    #                     "description": "The location to fetch the weather for. Should be a city name.",
    #                 },
    #             },
    #             "required": ["location"],
    #         },
    #     }
    # ]


@ai_function
async def get_weather_for_location(
    location: Annotated[str, "The city name for which to fetch the weather"],
) -> str:
    """Fetch the weather for a given location."""
    if location.lower() == "brampton":
        return "23.45C"
    else:
        return "no result found"


if __name__ == "__main__":
    # for testing
    import pprint

    pprint.pp(get_openai_function_schema())
