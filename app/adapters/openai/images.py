import base64
from typing import Any

from app import openai_pricing
from app.adapters.openai.gpt import openai_client
from app.ai import ImageModel
from app.ai import ImageResponse


async def generate(
    *,
    model: ImageModel,
    prompt: str,
    quality: str,
    size: str,
    background: str,
    reference_image: tuple[str, bytes, str] | None = None,
) -> ImageResponse:
    kwargs: dict[str, Any] = {
        "model": model.value,
        "prompt": prompt,
        "quality": quality,
        "size": size,
        "background": background,
        "output_format": "webp",
        "n": 1,
    }
    if reference_image is None:
        response = await openai_client.images.generate(**kwargs)
    else:
        response = await openai_client.images.edit(image=reference_image, **kwargs)
    usage = response.usage.model_dump() if response.usage is not None else None
    return ImageResponse(
        images=[
            base64.b64decode(item.b64_json)
            for item in response.data or []
            if item.b64_json
        ],
        usage=usage,
        estimated_cost_usd=openai_pricing.image_cost(usage),
    )
