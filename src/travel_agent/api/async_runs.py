from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from travel_agent.api.dependencies import get_application_service, get_principal
from travel_agent.application.models import RunHandle, TripRecord
from travel_agent.application.service import TravelApplicationService
from travel_agent.domain.models import PlanningRequest
from travel_agent.execution.models import AgentRunRecord, RunStatus
from travel_agent.identity.models import Principal


router = APIRouter(prefix="/api/v1", tags=["async-agent-runs"])
_TERMINAL = {
    RunStatus.COMPLETED,
    RunStatus.INTERRUPTED,
    RunStatus.REPLAYED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


@router.post("/trips", response_model=TripRecord, status_code=status.HTTP_201_CREATED)
async def create_trip(
    request: PlanningRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[TravelApplicationService, Depends(get_application_service)],
) -> TripRecord:
    return await service.create_trip(request, principal=principal)


@router.get("/trips/{trip_id}", response_model=TripRecord)
async def get_trip(
    trip_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[TravelApplicationService, Depends(get_application_service)],
) -> TripRecord:
    return await service.get_trip(trip_id, principal=principal)


@router.post(
    "/trips/{trip_id}/runs",
    response_model=RunHandle,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_trip_run(
    trip_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[TravelApplicationService, Depends(get_application_service)],
    request_id: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RunHandle:
    return await service.start_trip_run(
        trip_id, principal=principal, request_id=request_id
    )


@router.post("/runs/{run_id}/cancel", response_model=AgentRunRecord)
async def cancel_run(
    run_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[TravelApplicationService, Depends(get_application_service)],
) -> AgentRunRecord:
    return await service.cancel_run(run_id, principal=principal)


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[TravelApplicationService, Depends(get_application_service)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after_sequence: int = Query(default=0, ge=0),
) -> StreamingResponse:
    cursor = after_sequence
    if last_event_id and last_event_id.isdigit():
        cursor = max(cursor, int(last_event_id))

    async def events():
        nonlocal cursor
        while True:
            if await request.is_disconnected():
                return
            values = await service.get_trace(
                run_id,
                principal=principal,
                after_sequence=cursor,
                limit=200,
            )
            for event in values:
                cursor = event.sequence
                payload = json.dumps(
                    event.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                yield f"id: {event.sequence}\nevent: trace\ndata: {payload}\n\n"
            record = await service.get_run(run_id, principal=principal)
            if record.status in _TERMINAL and not values:
                yield "event: end\ndata: {}\n\n"
                return
            if not values:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
