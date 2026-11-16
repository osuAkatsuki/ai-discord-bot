from datetime import datetime
from datetime import timezone
from decimal import Decimal
from math import ceil
from pathlib import PurePosixPath

import openai

from app import settings
from app.adapters.openai import gpt
from app.ai import AIModel
from app.ai import ExecutionFile
from app.ai import ExecutionSession
from app.ai import SELECTABLE_MODELS


def validate_model(model: AIModel) -> None:
    if model not in SELECTABLE_MODELS:
        raise ValueError("Select a current model before enabling cloud execution.")


def _network_policy() -> dict:
    domains = sorted(set(settings.OPENAI_EXECUTION_ALLOWED_DOMAINS))
    if domains:
        return {"type": "allowlist", "allowed_domains": domains}
    return {"type": "disabled"}


async def create_session() -> ExecutionSession:
    container = await gpt.openai_client.with_options(max_retries=0).containers.create(
        name="discord-thread",
        memory_limit="1g",
        expires_after={"anchor": "last_active_at", "minutes": 20},
        network_policy=_network_policy(),
    )
    return ExecutionSession(
        container.id, datetime.fromtimestamp(container.created_at, timezone.utc)
    )


async def get_session(session_id: str) -> ExecutionSession | None:
    try:
        container = await gpt.openai_client.containers.retrieve(session_id)
    except openai.NotFoundError:
        return None
    if container.status == "expired":
        return None
    if container.status != "running":
        raise ValueError("The cloud workspace is unavailable. Try again shortly.")
    # Apply operator network policy changes to existing threads as well.
    policy = container.network_policy
    current = policy.model_dump(exclude_none=True) if policy else {"type": "disabled"}
    if current.get("type") == "allowlist":
        current["allowed_domains"] = sorted(set(current["allowed_domains"]))
    if current != _network_policy():
        await delete_session(session_id)
        return None
    return ExecutionSession(
        container.id, datetime.fromtimestamp(container.created_at, timezone.utc)
    )


async def delete_session(session_id: str) -> None:
    try:
        await gpt.openai_client.containers.delete(session_id)
    except openai.NotFoundError:
        pass


async def upload_file(session_id: str, filename: str, content: bytes) -> str:
    result = await gpt.openai_client.containers.files.create(
        session_id, file=(PurePosixPath(filename).name, content)
    )
    return result.path


async def download_file(file: ExecutionFile, max_bytes: int) -> bytes:
    metadata = await gpt.openai_client.containers.files.retrieve(
        file.id, container_id=file.session_id
    )
    if metadata.bytes is not None and metadata.bytes > max_bytes:
        raise ValueError("The generated file exceeds this channel's upload limit.")
    content = bytearray()
    async with gpt.openai_client.containers.files.content.with_streaming_response.retrieve(
        file.id, container_id=file.session_id
    ) as response:
        async for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ValueError(
                    "The generated file exceeds this channel's upload limit."
                )
    return bytes(content)


def session_cost(session: ExecutionSession) -> Decimal:
    # Standard 1 GB rate per started 20-minute session. An estimate of observed
    # lifetime, not an invoice; eligible minute billing can cost less.
    # https://developers.openai.com/api/docs/pricing (2026-10-13)
    elapsed = (datetime.now(timezone.utc) - session.created_at).total_seconds()
    return Decimal("0.03") * max(1, ceil(elapsed / (20 * 60)))
