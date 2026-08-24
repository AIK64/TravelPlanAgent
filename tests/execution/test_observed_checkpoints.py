from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from travel_agent.execution.checkpoints import ObservedCheckpointSaver


class FakeCheckpointDelegate:
    def __init__(self) -> None:
        self.serde = InMemorySaver().serde
        self.config_specs = ("thread_id",)
        self.fail: set[str] = set()

    def _result(self, operation: str, value=None):
        if operation in self.fail:
            raise RuntimeError(f"failed-{operation}")
        return value

    def get_next_version(self, current, channel):
        return f"{current}:{channel}"

    def get_tuple(self, _config):
        return self._result("get_tuple", None)

    async def aget_tuple(self, _config):
        return self._result("aget_tuple", None)

    def put(self, config, _checkpoint, _metadata, _versions):
        return self._result("put", config)

    async def aput(self, config, _checkpoint, _metadata, _versions):
        return self._result("aput", config)

    def put_writes(self, _config, _writes, _task_id, _task_path):
        self._result("put_writes")

    async def aput_writes(self, _config, _writes, _task_id, _task_path):
        self._result("aput_writes")

    def list(self, *_args, **_kwargs):
        yield "sync-item"

    async def alist(self, *_args, **_kwargs):
        yield "async-item"


@pytest.mark.asyncio
async def test_observed_checkpoint_saver_delegates_sync_and_async_operations() -> None:
    delegate = FakeCheckpointDelegate()
    saver = ObservedCheckpointSaver(delegate)
    config = {"configurable": {"thread_id": "thread-1"}}

    assert saver.config_specs == ("thread_id",)
    assert saver.get_next_version("v1", "channel") == "v1:channel"
    assert saver.get_tuple(config) is None
    assert await saver.aget_tuple(config) is None
    assert saver.put(config, {}, {}, {}) == config
    assert await saver.aput(config, {}, {}, {}) == config
    saver.put_writes(config, [("channel", "value")], "task-1")
    await saver.aput_writes(config, [("channel", "value")], "task-1")
    assert list(saver.list(config)) == ["sync-item"]
    assert [item async for item in saver.alist(config)] == ["async-item"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["get_tuple", "aget_tuple", "put", "aput", "put_writes", "aput_writes"],
)
async def test_observed_checkpoint_saver_propagates_delegate_failures(operation) -> None:
    delegate = FakeCheckpointDelegate()
    delegate.fail.add(operation)
    saver = ObservedCheckpointSaver(delegate)
    config = {"configurable": {"thread_id": "thread-1"}}

    with pytest.raises(RuntimeError, match=f"failed-{operation}"):
        if operation == "get_tuple":
            saver.get_tuple(config)
        elif operation == "aget_tuple":
            await saver.aget_tuple(config)
        elif operation == "put":
            saver.put(config, {}, {}, {})
        elif operation == "aput":
            await saver.aput(config, {}, {}, {})
        elif operation == "put_writes":
            saver.put_writes(config, [], "task-1")
        else:
            await saver.aput_writes(config, [], "task-1")
