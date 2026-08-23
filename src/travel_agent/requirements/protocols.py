from __future__ import annotations

from typing import Protocol, runtime_checkable

from travel_agent.requirements.models import (
    ClarificationModelInput,
    NaturalPlanningRequest,
    RequirementPatchProviderOutput,
    RequirementProviderOutput,
)


@runtime_checkable
class RequirementModel(Protocol):
    name: str
    model: str
    prompt_version: str
    clarification_prompt_version: str

    async def parse(
        self,
        request: NaturalPlanningRequest,
    ) -> RequirementProviderOutput:
        raise NotImplementedError

    async def parse_clarification(
        self,
        request: ClarificationModelInput,
    ) -> RequirementPatchProviderOutput:
        raise NotImplementedError
