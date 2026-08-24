from __future__ import annotations

import json
from typing import Any

from travel_agent.domain.lifecycle_models import EditModelInput, EditPatch, EditProviderOutput
from travel_agent.edits.errors import EditErrorCategory, EditProviderError, map_openai_compatible_error
from travel_agent.edits.prompts import EDIT_PROMPT_VERSION, EDIT_SYSTEM_PROMPT
from travel_agent.requirements.providers._compat import usage_value


class DeepSeekEditModel:
    name = "deepseek"
    prompt_version = EDIT_PROMPT_VERSION

    def __init__(self, *, client: Any, model: str, max_tokens: int = 1200) -> None:
        self._client = client
        self.model = model
        self._max_tokens = max_tokens

    async def parse(self, request: EditModelInput) -> EditProviderOutput:
        schema = json.dumps(EditPatch.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": f"{EDIT_SYSTEM_PROMPT}\nJSON Schema:\n{schema}"},
                    {"role": "user", "content": request.model_dump_json()},
                ],
                response_format={"type": "json_object"},
                max_tokens=self._max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as error:
            raise map_openai_compatible_error(error) from None
        choices = getattr(response, "choices", None)
        content = getattr(getattr(choices[0], "message", None), "content", None) if choices else None
        if not isinstance(content, str):
            raise EditProviderError(
                EditErrorCategory.INVALID_RESPONSE,
                "missing_structured_output",
                True,
                "计划编辑解析服务返回了无效结构",
            )
        try:
            patch = EditPatch.model_validate_json(content, strict=True)
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
            input_tokens=usage_value(usage, "prompt_tokens"),
            output_tokens=usage_value(usage, "completion_tokens"),
        )

