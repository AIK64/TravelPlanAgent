from __future__ import annotations

import json
from typing import Any

from travel_agent.requirements.errors import (
    RequirementErrorCategory,
    RequirementProviderError,
)
from travel_agent.requirements.models import (
    ClarificationModelInput,
    NaturalPlanningRequest,
    RequirementDraft,
    RequirementPatch,
    RequirementPatchProviderOutput,
    RequirementProviderOutput,
)
from travel_agent.requirements.prompts import (
    CLARIFICATION_PROMPT_VERSION,
    CLARIFICATION_SYSTEM_PROMPT,
    REQUIREMENT_PROMPT_VERSION,
    REQUIREMENT_SYSTEM_PROMPT,
)
from travel_agent.requirements.providers._compat import (
    map_openai_compatible_error,
    usage_value,
)


class OpenAIRequirementModel:
    """通过 Responses Structured Outputs 抽取 RequirementDraft。"""

    name = "openai"
    prompt_version = REQUIREMENT_PROMPT_VERSION
    clarification_prompt_version = CLARIFICATION_PROMPT_VERSION

    def __init__(self, *, client: Any, model: str) -> None:
        self._client = client
        self.model = model

    async def parse(
        self,
        request: NaturalPlanningRequest,
    ) -> RequirementProviderOutput:
        user_content = (
            f"reference_date={request.reference_date.isoformat()}\n"
            f"timezone={request.timezone}\n"
            f"requirement={request.text}"
        )
        try:
            response = await self._client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": REQUIREMENT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                text_format=RequirementDraft,
                store=False,
            )
        except RequirementProviderError:
            raise
        except Exception as error:
            raise map_openai_compatible_error(error) from None

        if getattr(response, "status", None) == "incomplete":
            raise RequirementProviderError(
                category=RequirementErrorCategory.INCOMPLETE,
                code="incomplete",
                retryable=True,
                safe_message="需求解析服务未能完成结构化输出",
            )
        if _has_refusal(getattr(response, "output", [])):
            raise RequirementProviderError(
                category=RequirementErrorCategory.REFUSAL,
                code="refusal",
                retryable=False,
                safe_message="需求解析服务拒绝处理该输入",
            )

        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise RequirementProviderError(
                category=RequirementErrorCategory.INVALID_RESPONSE,
                code="missing_structured_output",
                retryable=True,
                safe_message="需求解析服务返回了无效结构",
            )
        try:
            draft = (
                parsed
                if isinstance(parsed, RequirementDraft)
                else RequirementDraft.model_validate(parsed)
            )
        except Exception:
            raise RequirementProviderError(
                category=RequirementErrorCategory.INVALID_RESPONSE,
                code="invalid_structured_output",
                retryable=True,
                safe_message="需求解析服务返回了无效结构",
            ) from None

        usage = getattr(response, "usage", None)
        return RequirementProviderOutput(
            draft=draft,
            input_tokens=usage_value(usage, "input_tokens"),
            output_tokens=usage_value(usage, "output_tokens"),
        )

    async def parse_clarification(
        self,
        request: ClarificationModelInput,
    ) -> RequirementPatchProviderOutput:
        user_content = _clarification_user_content(request)
        try:
            response = await self._client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": CLARIFICATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                text_format=RequirementPatch,
                store=False,
            )
        except RequirementProviderError:
            raise
        except Exception as error:
            raise map_openai_compatible_error(error) from None

        _raise_for_response_status(response)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise _invalid_structured_output("missing_structured_output")
        try:
            patch = (
                parsed
                if isinstance(parsed, RequirementPatch)
                else RequirementPatch.model_validate(parsed)
            )
        except Exception:
            raise _invalid_structured_output("invalid_structured_output") from None
        usage = getattr(response, "usage", None)
        return RequirementPatchProviderOutput(
            patch=patch,
            input_tokens=usage_value(usage, "input_tokens"),
            output_tokens=usage_value(usage, "output_tokens"),
        )


def _has_refusal(output: object) -> bool:
    if not isinstance(output, list):
        return False
    for item in output:
        content = getattr(item, "content", None)
        if not isinstance(content, list):
            continue
        if any(
            getattr(part, "type", None) == "refusal"
            or getattr(part, "refusal", None) is not None
            for part in content
        ):
            return True
    return False


def _clarification_user_content(request: ClarificationModelInput) -> str:
    payload = {
        "reference_date": request.reference_date.isoformat(),
        "timezone": request.timezone,
        "target_fields": request.target_fields,
        "issues": [issue.model_dump(mode="json") for issue in request.issues],
        "current_draft": request.current_draft.model_dump(mode="json"),
        "answer": request.answer,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _raise_for_response_status(response: object) -> None:
    if getattr(response, "status", None) == "incomplete":
        raise RequirementProviderError(
            category=RequirementErrorCategory.INCOMPLETE,
            code="incomplete",
            retryable=True,
            safe_message="需求解析服务未能完成结构化输出",
        )
    if _has_refusal(getattr(response, "output", [])):
        raise RequirementProviderError(
            category=RequirementErrorCategory.REFUSAL,
            code="refusal",
            retryable=False,
            safe_message="需求解析服务拒绝处理该输入",
        )


def _invalid_structured_output(code: str) -> RequirementProviderError:
    return RequirementProviderError(
        category=RequirementErrorCategory.INVALID_RESPONSE,
        code=code,
        retryable=True,
        safe_message="需求解析服务返回了无效结构",
    )

