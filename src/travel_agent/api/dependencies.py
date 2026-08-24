from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from typing import Annotated

from travel_agent.identity.models import Principal
from travel_agent.memory.service import PreferenceMemoryService
from travel_agent.runtime import PlanningRuntime
from travel_agent.application.service import TravelApplicationService


def get_runtime(request: Request) -> PlanningRuntime:
    return request.app.state.planning_runtime


def get_principal(
    request: Request,
    tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    scopes: Annotated[str | None, Header(alias="X-Scopes")] = None,
) -> Principal:
    settings = request.app.state.settings
    if tenant_id is None and user_id is None and settings.dev_identity_enabled:
        tenant_id = settings.dev_tenant_id
        user_id = settings.dev_user_id
    if not tenant_id or not user_id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "authentication_required",
                "message": "authenticated tenant and user are required",
            },
        )
    resolved_scopes = frozenset(
        item.strip() for item in (scopes or "*").split(",") if item.strip()
    )
    return Principal(
        tenant_id=tenant_id,
        user_id=user_id,
        scopes=resolved_scopes,
        authentication_method="dev_header",
    )


def get_preference_service(
    runtime: Annotated[PlanningRuntime, Depends(get_runtime)],
) -> PreferenceMemoryService:
    if runtime.preference_service is None:
        raise RuntimeError("preference memory is not configured")
    return runtime.preference_service


def get_application_service(request: Request) -> TravelApplicationService:
    return request.app.state.travel_application_service
