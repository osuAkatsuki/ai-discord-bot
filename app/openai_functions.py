import itertools
import logging
import typing
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Annotated
from typing import TypeAlias
from typing import TypedDict

from app import settings
from app import state
from app._typing import UNSET
from app._typing import Unset
from app.adapters.openai.gpt import FunctionSchema
from app.adapters.openai.gpt import MessageContent

OpenAIFunctionCallback: TypeAlias = Callable[..., Awaitable[MessageContent]]


class OpenAIFunction(TypedDict):
    callback: OpenAIFunctionCallback
    schema: FunctionSchema


ai_functions: dict[str, OpenAIFunction] = {}


def translate_python_to_openai_type(python_type: type) -> str:
    if python_type is str:
        return "string"
    elif python_type is int:
        return "integer"
    else:
        raise NotImplementedError(f"Unsupported type python {python_type}")


def get_function_openai_schema(f: OpenAIFunctionCallback) -> FunctionSchema:
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

    hints_with_annotations = typing.get_type_hints(f, include_extras=True)
    function_defaults = f.__defaults__ or ()

    for (param_name, param_type), default_value in itertools.zip_longest(  # type: ignore
        hints_with_annotations.items(),
        function_defaults,
        fillvalue=UNSET,
    ):
        # skip the return type annotation
        if param_name == "return":
            continue

        # skip params lacking description annotations
        if (
            typing.get_origin(param_type) is not Annotated
            or len(param_type.__metadata__) != 1
        ):
            logging.warning(
                "Function decorated with @ai_function lacks parameter description annotation(s)",
                extra={
                    "param_name": param_name,
                    "param_type": param_type,
                    "param_type_origin": typing.get_origin(param_type),
                    "param_type_metadata": param_type.__metadata__,
                },
            )
            continue

        # add param to schema
        schema["parameters"]["properties"][param_name] = {
            "type": translate_python_to_openai_type(param_type.__origin__),
            "description": param_type.__metadata__[0],
        }

        # params without default values are requried
        if isinstance(default_value, Unset):
            schema["parameters"]["required"].append(param_name)

    return schema


def ai_function(f: OpenAIFunctionCallback) -> OpenAIFunctionCallback:
    ai_functions[f.__name__] = {
        "callback": f,
        "schema": get_function_openai_schema(f),
    }

    return f


def get_full_openai_functions_schema() -> list[FunctionSchema]:
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


# (coordinates don't change; might as well)
location_cache: dict[str, tuple[float, float]] = {}


def celcius_to_fahrenheit(degrees_celcius: float) -> float:
    return (degrees_celcius * 9 / 5) + 32.0


@ai_function
async def get_weather_for_location(
    location: Annotated[str, "The city name for which to fetch the weather"],
) -> MessageContent:
    """Fetch the weather for a given location."""
    cached = location_cache.get(location)
    if cached is not None:
        latitude, longitude = cached
    else:
        response = await state.http_client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": location, "key": settings.GOOGLE_PLACES_API_KEY},
        )
        response.raise_for_status()
        response_data = response.json()
        result = response_data["results"][0]

        latitude = result["geometry"]["location"]["lat"]
        longitude = result["geometry"]["location"]["lng"]

        location_cache[location] = (latitude, longitude)

    response = await state.http_client.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m",
            "timeformat": "unixtime",
        },
    )
    response.raise_for_status()
    response_data = response.json()

    degrees_celcius = response_data["hourly"]["temperature_2m"][-1]
    degrees_fahrenheit = celcius_to_fahrenheit(degrees_celcius)

    return {
        "type": "text",
        "text": f"{degrees_celcius}°C / {degrees_fahrenheit}°F",
    }
