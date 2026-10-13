import asyncio
import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app import ai
from app import main
from app import settings
from app.adapters.openai import execution
from app.adapters.openai import gpt
from app.usecases import cloud_execution


def output_response(output):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": ai.DEFAULT_AI_MODEL.value,
        "status": "completed",
        "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


def container_body(**kwargs):
    return {
        "id": "cntr_1",
        "object": "container",
        "name": "test",
        "created_at": int(datetime.now(timezone.utc).timestamp()),
        "status": "running",
        "network_policy": {"type": "disabled"},
        **kwargs,
    }


@pytest.mark.asyncio
async def test_shell_wire_uses_only_hosted_environment_and_scopes_artifacts(
    monkeypatch,
):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=output_response(
                [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "[report](sandbox:/mnt/data/report.csv)",
                                "annotations": [
                                    {
                                        "type": "container_file_citation",
                                        "container_id": container,
                                        "file_id": file_id,
                                        "filename": "report.csv",
                                        "start_index": 0,
                                        "end_index": 10,
                                    }
                                    for container, file_id in [
                                        ("cntr_1", "file_1"),
                                        ("other_thread", "file_2"),
                                    ]
                                ],
                            }
                        ],
                    }
                ]
            ),
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(gpt, "openai_client", client)
        response = await ai.send(
            model=ai.DEFAULT_AI_MODEL, messages=[], execution_session_id="cntr_1"
        )
        await ai.send(model=ai.DEFAULT_AI_MODEL, messages=[])
    assert requests[0]["tools"] == [
        {
            "type": "shell",
            "environment": {"type": "container_reference", "container_id": "cntr_1"},
        }
    ]
    assert "tools" not in requests[1]
    assert requests[0]["store"] is False
    assert requests[0]["max_tool_calls"] > 0
    assert response.files == (ai.ExecutionFile("cntr_1", "file_1", "report.csv"),)
    assert response.response_content == "report"


@pytest.mark.asyncio
async def test_container_creation_and_network_policy_changes(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(
                200,
                json={"id": "cntr_1", "object": "container.deleted", "deleted": True},
            )
        return httpx.Response(200, json=container_body())

    monkeypatch.setattr(settings, "OPENAI_EXECUTION_ALLOWED_DOMAINS", [])
    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(gpt, "openai_client", client)
        session = await ai.create_execution_session()
        assert (await ai.get_execution_session(session.id)).id == session.id
        monkeypatch.setattr(
            settings, "OPENAI_EXECUTION_ALLOWED_DOMAINS", ["github.com"]
        )
        assert await ai.get_execution_session(session.id) is None
    assert json.loads(requests[0].content)["network_policy"] == {"type": "disabled"}
    assert [r.method for r in requests] == ["POST", "GET", "GET", "DELETE"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 403, 200])
async def test_expiration_is_recoverable_but_access_errors_are_not(monkeypatch, status):
    def handle(request):
        return httpx.Response(
            status,
            json=(
                container_body(status="expired")
                if status == 200
                else {"error": {"message": "unavailable"}}
            ),
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(gpt, "openai_client", client)
        if status == 403:
            with pytest.raises(openai.PermissionDeniedError):
                await ai.get_execution_session("cntr_1")
        else:
            assert await ai.get_execution_session("cntr_1") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reported_size", [None, 1])
async def test_artifact_download_is_bounded_even_if_metadata_is_wrong(
    monkeypatch, reported_size
):
    def handle(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"0123456789")
        return httpx.Response(
            200,
            json={
                "id": "file_1",
                "container_id": "cntr_1",
                "object": "container.file",
                "path": "/mnt/data/a.txt",
                "created_at": 1,
                "bytes": reported_size,
                "source": "assistant",
            },
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(gpt, "openai_client", client)
        file = ai.ExecutionFile("cntr_1", "file_1", "a.txt")
        with pytest.raises(ValueError):
            await ai.download_execution_file(file, 5)
        assert await ai.download_execution_file(file, 10) == b"0123456789"


@pytest.mark.asyncio
async def test_reuse_expiry_and_thread_isolation(monkeypatch):
    existing = ai.ExecutionSession("existing", datetime.now(timezone.utc))
    fresh = ai.ExecutionSession("fresh", datetime.now(timezone.utc))
    get = AsyncMock(side_effect=[existing, None])
    create = AsyncMock(return_value=fresh)
    persist = AsyncMock()
    costs = AsyncMock()
    monkeypatch.setattr(ai, "get_execution_session", get)
    monkeypatch.setattr(ai, "create_execution_session", create)
    monkeypatch.setattr(cloud_execution.threads, "set_execution_session", persist)
    monkeypatch.setattr(cloud_execution, "record_cost", costs)
    thread = SimpleNamespace(
        thread_id=100, model=ai.DEFAULT_AI_MODEL, execution_session_id="existing"
    )
    session, notice = await cloud_execution.prepare_session(thread, 5)
    assert session == existing and notice is None
    create.assert_not_awaited()
    session, notice = await cloud_execution.prepare_session(thread, 5)
    assert session == fresh and notice
    persist.assert_awaited_once_with(100, "fresh")
    other = SimpleNamespace(
        thread_id=101, model=ai.DEFAULT_AI_MODEL, execution_session_id=None
    )
    await cloud_execution.prepare_session(other, 6)
    assert create.await_count == 2
    assert persist.call_args.args == (101, "fresh")
    assert [call.args[0] for call in costs.call_args_list] == [100, 100, 101]


@pytest.mark.asyncio
async def test_failed_session_persistence_cleans_up_container(monkeypatch):
    session = ai.ExecutionSession("new", datetime.now(timezone.utc))
    monkeypatch.setattr(ai, "create_execution_session", AsyncMock(return_value=session))
    monkeypatch.setattr(
        cloud_execution.threads,
        "set_execution_session",
        AsyncMock(side_effect=RuntimeError("DB unavailable")),
    )
    delete = AsyncMock()
    monkeypatch.setattr(ai, "delete_execution_session", delete)
    thread = SimpleNamespace(
        thread_id=1, model=ai.DEFAULT_AI_MODEL, execution_session_id=None
    )
    with pytest.raises(RuntimeError):
        await cloud_execution.prepare_session(thread, 2)
    delete.assert_awaited_once_with("new")


@pytest.mark.asyncio
async def test_reset_and_disable_delete_workspace_before_updating_state(monkeypatch):
    events = []

    async def delete(session_id):
        events.append(("delete", session_id))

    async def persist(thread_id, session_id):
        events.append(("persist", session_id))

    async def enable(thread_id, enabled):
        events.append(("enable", enabled))

    monkeypatch.setattr(ai, "delete_execution_session", delete)
    monkeypatch.setattr(cloud_execution.threads, "set_execution_session", persist)
    monkeypatch.setattr(cloud_execution.threads, "set_cloud_execution", enable)
    thread = SimpleNamespace(
        thread_id=1, model=ai.DEFAULT_AI_MODEL, execution_session_id="old"
    )
    await cloud_execution.configure(thread, True, True)
    assert events == [("delete", "old"), ("persist", None), ("enable", True)]
    events.clear()
    await cloud_execution.configure(thread, False, False)
    assert events == [("delete", "old"), ("persist", None), ("enable", False)]


@pytest.mark.asyncio
async def test_thread_lock_serializes_shared_workspaces():
    entered = asyncio.Event()
    release = asyncio.Event()
    second_entered = asyncio.Event()

    async def first():
        async with cloud_execution.thread_lock(100):
            entered.set()
            await release.wait()

    async def second():
        async with cloud_execution.thread_lock(100):
            second_entered.set()

    task1 = asyncio.create_task(first())
    await entered.wait()
    task2 = asyncio.create_task(second())
    await asyncio.sleep(0)
    assert not second_entered.is_set()
    async with cloud_execution.thread_lock(101):
        release.set()
    await asyncio.gather(task1, task2)
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_execution_command_rejects_unauthorized_users_before_any_provider_call(
    monkeypatch,
):
    configure = AsyncMock()
    monkeypatch.setattr(cloud_execution, "configure", configure)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=0), response=SimpleNamespace(send_message=AsyncMock())
    )
    await main.execution.callback(interaction, enabled=True)
    configure.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


def test_execution_cost_counts_started_intervals_not_requests():
    now = datetime.now(timezone.utc)
    fresh = ai.ExecutionSession("one", now)
    active = ai.ExecutionSession("one", now - timedelta(minutes=21))
    assert ai.execution_session_cost(active) == 2 * ai.execution_session_cost(fresh)
    assert ai.execution_session_cost(fresh) == ai.execution_session_cost(fresh)


@pytest.mark.asyncio
async def test_shell_http_errors_are_not_retried(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(500, json={"error": {"message": "temporary error"}})

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(gpt, "openai_client", client)
        with pytest.raises(openai.InternalServerError):
            await ai.send(
                model=ai.DEFAULT_AI_MODEL, messages=[], execution_session_id="cntr_1"
            )
    assert len(requests) == 1  # Replaying the request could rerun commands.


@pytest.mark.asyncio
async def test_thread_uploads_files_and_passes_only_remote_session_to_model(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from app.errors import Error
    from app.errors import ErrorCode
    from app.usecases import ai_conversations

    @asynccontextmanager
    async def typing():
        yield

    bot_user = SimpleNamespace(id=999, mention="<@999>")
    message = SimpleNamespace(
        author=SimpleNamespace(id=285190493703503872),
        mentions=[bot_user],
        clean_content="analyze this",
        channel=SimpleNamespace(id=100, typing=typing, send=AsyncMock()),
        attachments=[
            SimpleNamespace(
                id=42,
                filename="../data.zip",
                size=10,
                read=AsyncMock(return_value=b"archive"),
            )
        ],
    )
    thread = SimpleNamespace(
        thread_id=100,
        model=ai.DEFAULT_AI_MODEL,
        context_length=5,
        reasoning_effort="low",
        web_search=False,
        cloud_execution=True,
    )
    session = ai.ExecutionSession("cntr_1", datetime.now(timezone.utc))
    monkeypatch.setattr(
        ai_conversations.threads, "fetch_one", AsyncMock(return_value=thread)
    )
    monkeypatch.setattr(
        ai_conversations.thread_messages, "fetch_many", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(ai_conversations.thread_messages, "create", AsyncMock())
    prepare = AsyncMock(return_value=(session, "Workspace was replaced."))
    costs = AsyncMock()
    monkeypatch.setattr(cloud_execution, "prepare_session", prepare)
    monkeypatch.setattr(cloud_execution, "record_cost", costs)
    upload = AsyncMock(return_value="/mnt/data/42_data.zip")
    monkeypatch.setattr(ai, "upload_execution_file", upload)
    file = ai.ExecutionFile("cntr_1", "file_1", "answer.txt")
    request = AsyncMock(
        return_value=ai_conversations.AIRequestResponse("done", 10, 5, (file,))
    )
    monkeypatch.setattr(ai_conversations, "make_ai_request", request)
    result = await ai_conversations.send_message_to_thread(
        SimpleNamespace(user=bot_user), message
    )
    assert result.files == (file,)
    upload.assert_awaited_once_with("cntr_1", "42_data.zip", b"archive")
    assert request.call_args.kwargs["execution_session_id"] == "cntr_1"
    assert (
        "/mnt/data/42_data.zip" in request.call_args.args[0][-1]["content"][-1]["text"]
    )
    message.channel.send.assert_awaited_once()
    costs.assert_awaited_once_with(100, message.author.id, session)
    # Unsuccessful model responses also consume workspace time.
    request.return_value = Error(code=ErrorCode.UNEXPECTED_ERROR, messages=["failed"])
    assert isinstance(
        await ai_conversations.send_message_to_thread(
            SimpleNamespace(user=bot_user), message
        ),
        Error,
    )
    assert costs.await_count == 2


@pytest.mark.asyncio
async def test_upload_uses_container_file_api_and_drops_local_path(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "file_1",
                "container_id": "cntr_1",
                "object": "container.file",
                "path": "/mnt/data/script.py",
                "created_at": 1,
                "bytes": 8,
                "source": "user",
            },
        )

    async with openai.AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as client:
        monkeypatch.setattr(gpt, "openai_client", client)
        assert (
            await ai.upload_execution_file("cntr_1", "../script.py", b"print(1)")
            == "/mnt/data/script.py"
        )
    assert requests[0].url.path.endswith("/containers/cntr_1/files")
    assert b'filename="script.py"' in requests[0].content
    assert b"print(1)" in requests[0].content


def test_cloud_commands_serialize_for_discord():
    commands = {
        c.name: c.to_dict(main.command_tree) for c in main.command_tree.get_commands()
    }
    assert {o["name"] for o in commands["devexecution"]["options"]} == {
        "enabled",
        "reset",
    }
    assert "cloud_execution" in {o["name"] for o in commands["devai"]["options"]}
