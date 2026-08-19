from fastapi import FastAPI
from fastapi.responses import JSONResponse

from travel_agent.api.routes import router
from travel_agent.logging_config import configure_logging


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="Constraint-Aware Travel Agent",
        version="0.1.0",
        default_response_class=UTF8JSONResponse,
        description=(
            "A deterministic first slice of a stateful "
            "Plan-Execute-Validate-Replan travel agent."
        ),
    )
    app.include_router(router)
    return app


app = create_app()
