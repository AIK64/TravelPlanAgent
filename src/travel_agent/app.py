from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from travel_agent import __version__
from travel_agent.api.errors import (
    UTF8JSONResponse,
    clarification_conflict_exception_handler,
    clarification_not_found_exception_handler,
    tool_unavailable_exception_handler,
    requirement_unavailable_exception_handler,
    edit_unavailable_exception_handler,
    lifecycle_action_exception_handler,
    lifecycle_conflict_exception_handler,
    lifecycle_not_found_exception_handler,
    weather_unavailable_exception_handler,
    execution_budget_exception_handler,
    run_not_found_exception_handler,
)
from travel_agent.api.routes import router
from travel_agent.config import Settings
from travel_agent.logging_config import configure_logging
from travel_agent.runtime import PlanningRuntime
from travel_agent.tools.errors import ToolUnavailableError
from travel_agent.requirements.errors import RequirementUnavailableError
from travel_agent.requirements.errors import (
    ClarificationResumeConflictError,
    ClarificationThreadNotFoundError,
)
from travel_agent.edits.errors import EditUnavailableError
from travel_agent.lifecycle.errors import (
    LifecycleActionError,
    LifecycleConflictError,
    LifecycleNotFoundError,
)
from travel_agent.weather.errors import WeatherUnavailableError
from travel_agent.execution.errors import ExecutionBudgetExceeded, RunNotFoundError


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
            "A grounded natural-language and structured-input travel agent with "
            "explicit Parse-Tool-Validate-Replan trajectories."
        ),
        lifespan=lifespan,
    )
    app.add_exception_handler(
        ToolUnavailableError,
        tool_unavailable_exception_handler,
    )
    app.add_exception_handler(
        RequirementUnavailableError,
        requirement_unavailable_exception_handler,
    )
    app.add_exception_handler(
        ClarificationThreadNotFoundError,
        clarification_not_found_exception_handler,
    )
    app.add_exception_handler(
        ClarificationResumeConflictError,
        clarification_conflict_exception_handler,
    )
    app.add_exception_handler(EditUnavailableError, edit_unavailable_exception_handler)
    app.add_exception_handler(
        LifecycleNotFoundError, lifecycle_not_found_exception_handler
    )
    app.add_exception_handler(
        LifecycleConflictError, lifecycle_conflict_exception_handler
    )
    app.add_exception_handler(
        LifecycleActionError, lifecycle_action_exception_handler
    )
    app.add_exception_handler(
        WeatherUnavailableError, weather_unavailable_exception_handler
    )
    app.add_exception_handler(
        ExecutionBudgetExceeded, execution_budget_exception_handler
    )
    app.add_exception_handler(RunNotFoundError, run_not_found_exception_handler)
    app.include_router(router)
    return app


app = create_app()
