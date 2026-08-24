from __future__ import annotations

import json
from typing import Any

from travel_agent.domain.lifecycle_models import EditModelInput, EditPatch, EditProviderOutput
from travel_agent.edits.errors import EditErrorCategory, EditProviderError, map_openai_compatible_error
from travel_agent.edits.prompts import EDIT_PROMPT_VERSION, EDIT_SYSTEM_PROMPT
from travel_agent.requirements.providers._compat import usage_value


class OpenAIEditModel:
    name = "openai"
    prompt_version = EDIT_PROMPT_VERSION

    def __init__(self, *, client: Any, model: str) -> None:
        self._client = client
        self.model = model

    async def parse(self, request: EditModelInput) -> EditProviderOutput:
        try:
            response = await self._client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": EDIT_SYSTEM_PROMPT},
                    {"role": "user", "content": request.model_dump_json()},
                ],
                text_format=EditPatch,
                store=False,
            )
        except Exception as error:
            raise map_openai_compatible_error(error) from None
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise EditProviderError(
                EditErrorCategory.INVALID_RESPONSE,
                "missing_structured_output",
                True,
                "计划编辑解析服务返回了无效结构",
            )
        try:
            patch = parsed if isinstance(parsed, EditPatch) else EditPatch.model_validate(parsed)
        except Exception:
            raise EditProviderError(
                EditErrorCategory.INVALID_RESPONSE,
                "invalid_structured_output",
                True,
                "计划编辑解析服务返回了无效结构",
            ) from None
        usage = getattr(response, "usage", None)
        return EditProviderOutput(
            patch=patch,
            input_tokens=usage_value(usage, "input_tokens"),
            output_tokens=usage_value(usage, "output_tokens"),
        )

