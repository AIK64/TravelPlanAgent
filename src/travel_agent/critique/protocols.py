from __future__ import annotations

from typing import Protocol, runtime_checkable

from travel_agent.domain.critique_models import (
    SoftCriticProviderOutput,
    SoftCriticRequest,
)


@runtime_checkable
class CriticModel(Protocol):
    name: str
    model: str
    prompt_version: str

    async def critique(
        self,
        request: SoftCriticRequest,
    ) -> SoftCriticProviderOutput:
        raise NotImplementedError

