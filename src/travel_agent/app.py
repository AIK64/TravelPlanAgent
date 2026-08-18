from fastapi import FastAPI

from travel_agent.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Constraint-Aware Travel Agent",
        version="0.1.0",
        description=(
            "A deterministic first slice of a stateful "
            "Plan-Execute-Validate-Replan travel agent."
        ),
    )
    app.include_router(router)
    return app


app = create_app()

