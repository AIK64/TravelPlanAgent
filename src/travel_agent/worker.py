from __future__ import annotations

import asyncio
import logging
import signal

from travel_agent.config import AsyncExecutionBackend, Settings
from travel_agent.infrastructure.queue import open_run_queue
from travel_agent.logging_config import configure_logging
from travel_agent.runtime import PlanningRuntime


logger = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    if settings.async_execution_backend is not AsyncExecutionBackend.REDIS:
        raise RuntimeError("worker requires ASYNC_EXECUTION_BACKEND=redis")
    runtime = await PlanningRuntime.create(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop.set)
        except NotImplementedError:
            pass
    try:
        async with open_run_queue(settings.redis_url) as queue:
            logger.info("worker.started | backend=redis")
            while not stop.is_set():
                job = await queue.dequeue(timeout_seconds=5)
                if job is None:
                    continue
                logger.info(
                    "worker.run_started | run_id=%s trip_id=%s", job.run_id, job.trip_id
                )
                try:
                    await runtime.execute_plan(
                        job.request,
                        thread_id=job.thread_id,
                        run_id=job.run_id,
                        principal=job.principal,
                        precreated=True,
                    )
                except BaseException:
                    logger.exception("worker.run_failed | run_id=%s", job.run_id)
                else:
                    logger.info("worker.run_completed | run_id=%s", job.run_id)
    finally:
        await runtime.close()


def main() -> None:
    configure_logging()
    asyncio.run(run_worker(Settings.from_env()))


if __name__ == "__main__":
    main()
