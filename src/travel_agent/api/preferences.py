from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from travel_agent.api.dependencies import get_preference_service, get_principal
from travel_agent.identity.models import Principal
from travel_agent.memory.models import (
    MemoryProposal,
    MemoryProposalRequest,
    PersonalizationSettings,
    PersonalizationUpdateRequest,
    PreferenceCreateRequest,
    PreferenceExport,
    PreferenceList,
    PreferenceMemory,
    PreferenceUpdateRequest,
    ProposalDecisionRequest,
)
from travel_agent.memory.service import PreferenceMemoryService


router = APIRouter(prefix="/api/v1", tags=["preference-memory"])


@router.get("/preferences", response_model=PreferenceList)
async def list_preferences(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
    include_inactive: bool = Query(default=False),
) -> PreferenceList:
    return await service.list(principal, include_inactive=include_inactive)


@router.post(
    "/preferences",
    response_model=PreferenceMemory,
    status_code=status.HTTP_201_CREATED,
)
async def create_preference(
    request: PreferenceCreateRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> PreferenceMemory:
    return await service.create_explicit(principal, request)


@router.get("/preferences/export", response_model=PreferenceExport)
async def export_preferences(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> PreferenceExport:
    return await service.export(principal)


@router.patch("/preferences/{memory_id}", response_model=PreferenceMemory)
async def update_preference(
    memory_id: str,
    request: PreferenceUpdateRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> PreferenceMemory:
    return await service.update(principal, memory_id, request)


@router.post("/preferences/{memory_id}/revoke", response_model=PreferenceMemory)
async def revoke_preference(
    memory_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
    expected_revision: int = Query(ge=1),
) -> PreferenceMemory:
    return await service.revoke(
        principal, memory_id, expected_revision=expected_revision
    )


@router.delete(
    "/preferences/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_preference(
    memory_id: str,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> Response:
    await service.delete(principal, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/preferences/clear")
async def clear_preferences(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> dict[str, int]:
    return {"deleted_count": await service.clear(principal)}


@router.post(
    "/preferences/proposals",
    response_model=MemoryProposal,
    status_code=status.HTTP_201_CREATED,
)
async def create_memory_proposal(
    request: MemoryProposalRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> MemoryProposal:
    return await service.propose(principal, request)


@router.post(
    "/preferences/proposals/{proposal_id}/confirm",
    response_model=PreferenceMemory,
)
async def confirm_memory_proposal(
    proposal_id: str,
    request: ProposalDecisionRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> PreferenceMemory:
    return await service.confirm_proposal(
        principal, proposal_id, request_id=request.request_id
    )


@router.post(
    "/preferences/proposals/{proposal_id}/reject",
    response_model=MemoryProposal,
)
async def reject_memory_proposal(
    proposal_id: str,
    request: ProposalDecisionRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> MemoryProposal:
    return await service.reject_proposal(
        principal, proposal_id, request_id=request.request_id
    )


@router.get("/profile/personalization", response_model=PersonalizationSettings)
async def get_personalization(
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> PersonalizationSettings:
    return await service.personalization(principal)


@router.patch("/profile/personalization", response_model=PersonalizationSettings)
async def update_personalization(
    request: PersonalizationUpdateRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    service: Annotated[PreferenceMemoryService, Depends(get_preference_service)],
) -> PersonalizationSettings:
    return await service.update_personalization(principal, request)
