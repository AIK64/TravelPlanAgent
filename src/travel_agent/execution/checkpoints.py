from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)

from travel_agent.execution.context import match_fault, record_checkpoint
from travel_agent.execution.faults import FaultMode, FaultPoint


class ObservedCheckpointSaver(BaseCheckpointSaver):
    """透明代理 LangGraph Checkpointer，并将读写纳入当前 AgentRun。"""

    def __init__(self, delegate: BaseCheckpointSaver) -> None:
        super().__init__(serde=delegate.serde)
        self._delegate = delegate

    @property
    def config_specs(self):
        return self._delegate.config_specs

    def get_next_version(self, current: Any, channel: Any) -> Any:
        return self._delegate.get_next_version(current, channel)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        try:
            self._inject(FaultPoint.CHECKPOINT_READ, "get_tuple")
            value = self._delegate.get_tuple(config)
        except BaseException:
            record_checkpoint("get_tuple", success=False, write=False)
            raise
        record_checkpoint("get_tuple", success=True, write=False)
        return value

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        try:
            self._inject(FaultPoint.CHECKPOINT_READ, "aget_tuple")
            value = await self._delegate.aget_tuple(config)
        except BaseException:
            record_checkpoint("aget_tuple", success=False, write=False)
            raise
        record_checkpoint("aget_tuple", success=True, write=False)
        return value

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        try:
            self._before_write("put")
            result = self._delegate.put(config, checkpoint, metadata, new_versions)
        except BaseException:
            record_checkpoint("put", success=False, write=False)
            raise
        record_checkpoint("put", success=True, write=True)
        return result

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        try:
            self._before_write("aput")
            result = await self._delegate.aput(config, checkpoint, metadata, new_versions)
        except BaseException:
            record_checkpoint("aput", success=False, write=False)
            raise
        record_checkpoint("aput", success=True, write=True)
        return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        try:
            self._before_write("put_writes")
            self._delegate.put_writes(config, writes, task_id, task_path)
        except BaseException:
            record_checkpoint("put_writes", success=False, write=False)
            raise
        record_checkpoint("put_writes", success=True, write=True)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        try:
            self._before_write("aput_writes")
            await self._delegate.aput_writes(config, writes, task_id, task_path)
        except BaseException:
            record_checkpoint("aput_writes", success=False, write=False)
            raise
        record_checkpoint("aput_writes", success=True, write=True)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        yield from self._delegate.list(
            config, filter=filter, before=before, limit=limit
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        async for item in self._delegate.alist(
            config, filter=filter, before=before, limit=limit
        ):
            yield item

    def _before_write(self, operation: str) -> None:
        self._inject(FaultPoint.CHECKPOINT_WRITE, operation)

    @staticmethod
    def _inject(point: FaultPoint, operation: str) -> None:
        mode = match_fault(point, operation=operation)
        if mode in {FaultMode.WRITE_FAILURE, FaultMode.CONNECTION_ERROR, FaultMode.TIMEOUT}:
            raise CheckpointInjectedError(
                f"injected checkpoint failure: {mode.value}"
            )


class CheckpointInjectedError(RuntimeError):
    pass
