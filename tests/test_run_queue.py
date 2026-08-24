from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from travel_agent.config import Settings
from travel_agent.domain.models import PlanningRequest
from travel_agent.identity.models import Principal
from travel_agent.infrastructure.queue import RedisRunQueue, RunJob
from travel_agent.worker import run_worker


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.operations: list[tuple[str, tuple[object, ...]]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    def set(self, *args, **_kwargs) -> "FakePipeline":
        self.operations.append(("set", args))
        return self

    def lpush(self, *args) -> "FakePipeline":
        self.operations.append(("lpush", args))
        return self

    def lrem(self, *args) -> "FakePipeline":
        self.operations.append(("lrem", args))
        return self

    def delete(self, *args) -> "FakePipeline":
        self.operations.append(("delete", args))
        return self

    async def execute(self) -> list[int | bool]:
        results: list[int | bool] = []
        for operation, args in self.operations:
            if operation == "set":
                self.redis.values[str(args[0])] = args[1]
                results.append(True)
            elif operation == "lpush":
                self.redis.items.insert(0, args[1])
                results.append(len(self.redis.items))
            elif operation == "lrem":
                try:
                    self.redis.items.remove(args[2])
                    results.append(1)
                except ValueError:
                    results.append(0)
            else:
                results.append(int(self.redis.values.pop(str(args[0]), None) is not None))
        return results


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.items: list[object] = []
        self.closed = False

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction is True
        return FakePipeline(self)

    async def brpop(self, _name: str, *, timeout: int):
        assert timeout >= 0
        if not self.items:
            return None
        return b"travel-agent:runs", self.items.pop()

    async def get(self, key: str):
        return self.values.get(key)

    async def delete(self, key: str):
        return int(self.values.pop(key, None) is not None)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_redis_run_queue_round_trip_remove_and_close(hangzhou_trip) -> None:
    redis = FakeRedis()
    queue = RedisRunQueue(redis)
    job = RunJob(
        run_id="run-1",
        trip_id="trip-1",
        thread_id="thread-1",
        request=PlanningRequest(trip=hangzhou_trip),
        principal=Principal(tenant_id="tenant-a", user_id="user-a"),
    )

    assert await queue.dequeue(timeout_seconds=0) is None
    assert await queue.remove("missing") is False
    await queue.enqueue(job)
    assert await queue.remove("run-1") is True

    await queue.enqueue(job)
    restored = await queue.dequeue(timeout_seconds=1)
    assert restored == job
    assert await queue.remove("run-1") is False
    await queue.close()
    assert redis.closed is True


@pytest.mark.asyncio
async def test_worker_executes_reserved_job_and_closes_runtime(
    hangzhou_trip, monkeypatch
) -> None:
    job = RunJob(
        run_id="run-worker",
        trip_id="trip-worker",
        thread_id="thread-worker",
        request=PlanningRequest(trip=hangzhou_trip),
        principal=Principal(tenant_id="tenant-a", user_id="user-a"),
    )

    class FakeQueue:
        calls = 0

        async def dequeue(self, *, timeout_seconds: int):
            self.calls += 1
            if self.calls == 1:
                return None
            if self.calls == 2:
                return job
            raise RuntimeError("stop-test-worker")

    class FakeRuntime:
        executed = []
        closed = False

        async def execute_plan(self, request, **kwargs):
            self.executed.append((request, kwargs))

        async def close(self):
            self.closed = True

    runtime = FakeRuntime()

    async def create_runtime(_settings):
        return runtime

    @asynccontextmanager
    async def queue_context(_url):
        yield FakeQueue()

    monkeypatch.setattr(
        "travel_agent.worker.PlanningRuntime.create", create_runtime
    )
    monkeypatch.setattr("travel_agent.worker.open_run_queue", queue_context)
    settings = Settings.from_env({"ASYNC_EXECUTION_BACKEND": "redis"})

    with pytest.raises(RuntimeError, match="stop-test-worker"):
        await run_worker(settings)

    assert runtime.closed is True
    assert len(runtime.executed) == 1
    _, kwargs = runtime.executed[0]
    assert kwargs["run_id"] == "run-worker"
    assert kwargs["precreated"] is True


@pytest.mark.asyncio
async def test_worker_rejects_local_backend() -> None:
    with pytest.raises(RuntimeError, match="requires"):
        await run_worker(Settings.from_env({}))
