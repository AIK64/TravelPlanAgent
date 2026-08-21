from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from travel_agent import __version__
from travel_agent.api.errors import (
    UTF8JSONResponse,
    tool_unavailable_exception_handler,
)
from travel_agent.api.routes import router
from travel_agent.config import Settings
from travel_agent.logging_config import configure_logging
from travel_agent.runtime import PlanningRuntime
from travel_agent.tools.errors import ToolUnavailableError


RuntimeFactory = Callable[[Settings], Awaitable[PlanningRuntime]]


def create_app(
    settings: Settings | None = None,
    runtime_factory: RuntimeFactory = PlanningRuntime.create,
) -> FastAPI:
    configure_logging()
    resolved_settings = settings or Settings.from_env()
    resolved_settings.validate()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        runtime = await runtime_factory(resolved_settings)
        application.state.planning_runtime = runtime
        try:
            yield
        finally:
            await runtime.close()

    app = FastAPI(
        title="Constraint-Aware Travel Agent",
        version=__version__,
        default_response_class=UTF8JSONResponse,
        description=(
            "A deterministic first slice of a stateful "
            "Plan-Execute-Validate-Replan travel agent."
        ),
        lifespan=lifespan,
    )
    app.add_exception_handler(
        ToolUnavailableError,
        tool_unavailable_exception_handler,
    )
    app.include_router(router)
    return app


app = create_app()
