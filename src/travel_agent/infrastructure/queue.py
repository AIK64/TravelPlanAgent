from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from travel_agent.domain.models import PlanningRequest
from travel_agent.identity.models import Principal


class RunJob(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    trip_id: str
    thread_id: str
    request: PlanningRequest
    principal: Principal
    request_id: str | None = None


class RunQueue(Protocol):
    async def enqueue(self, job: RunJob) -> None: ...
    async def dequeue(self, *, timeout_seconds: int = 5) -> RunJob | None: ...
    async def remove(self, run_id: str) -> bool: ...
    async def close(self) -> None: ...


class RedisRunQueue:
    """显式 Job Payload 的 Redis 队列；业务状态仍以 Run Store 为准。"""

    def __init__(self, client, *, queue_name: str = "travel-agent:runs") -> None:
        self.client = client
        self.queue_name = queue_name

    async def enqueue(self, job: RunJob) -> None:
        payload = job.model_dump_json()
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.set(self._job_key(job.run_id), payload, ex=86_400)
            pipe.lpush(self.queue_name, payload)
            await pipe.execute()

    async def dequeue(self, *, timeout_seconds: int = 5) -> RunJob | None:
        result = await self.client.brpop(self.queue_name, timeout=timeout_seconds)
        if result is None:
            return None
        payload = result[1]
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        job = RunJob.model_validate_json(payload)
        await self.client.delete(self._job_key(job.run_id))
        return job

    async def remove(self, run_id: str) -> bool:
        payload = await self.client.get(self._job_key(run_id))
        if payload is None:
            return False
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.lrem(self.queue_name, 1, payload)
            pipe.delete(self._job_key(run_id))
            removed, _ = await pipe.execute()
        return bool(removed)

    async def close(self) -> None:
        await self.client.aclose()

    @staticmethod
    def _job_key(run_id: str) -> str:
        return f"travel-agent:run-job:{run_id}"


@asynccontextmanager
async def open_run_queue(redis_url: str) -> AsyncIterator[RunQueue]:
    try:
        from redis.asyncio import Redis
    except ImportError as error:
        raise RuntimeError(
            "Install the production extra to use ASYNC_EXECUTION_BACKEND=redis"
        ) from error
    queue = RedisRunQueue(Redis.from_url(redis_url))
    try:
        yield queue
    finally:
        await queue.close()
