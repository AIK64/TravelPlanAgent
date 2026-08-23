from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from travel_agent.config import CheckpointBackend, Settings
from travel_agent.requirements.workflow import requirement_checkpoint_serializer


@asynccontextmanager
async def open_requirement_checkpointer(
    settings: Settings,
) -> AsyncIterator[BaseCheckpointSaver]:
    """按配置持有 Requirement Graph 的 Checkpointer 生命周期。"""
    serde = requirement_checkpoint_serializer()
    if settings.checkpoint_backend is CheckpointBackend.MEMORY:
        yield InMemorySaver(serde=serde)
        return

    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError as error:
        raise RuntimeError(
            "Install the checkpoint-sqlite extra to use CHECKPOINT_BACKEND=sqlite"
        ) from error

    database_path = Path(settings.checkpoint_sqlite_path).expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(database_path)) as connection:
        saver = AsyncSqliteSaver(connection, serde=serde)
        await saver.setup()
        yield saver
