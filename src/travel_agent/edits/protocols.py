from __future__ import annotations

from typing import Protocol

from travel_agent.domain.lifecycle_models import EditModelInput, EditProviderOutput


class EditModel(Protocol):
    name: str
    model: str
    prompt_version: str

    async def parse(self, request: EditModelInput) -> EditProviderOutput: ...

