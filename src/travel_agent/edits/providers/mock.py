from __future__ import annotations

import re

from travel_agent.domain.lifecycle_models import (
    EditModelInput,
    EditOperation,
    EditOperationKind,
    EditPatch,
    EditProviderOutput,
)
from travel_agent.edits.prompts import EDIT_PROMPT_VERSION


_CHINESE_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}


class MockEditModel:
    name = "mock"
    model = "mock-plan-edit-v1"
    prompt_version = EDIT_PROMPT_VERSION

    async def parse(self, request: EditModelInput) -> EditProviderOutput:
        text = request.text
        matched = next(
            (item for item in request.items if item.name.casefold() in text.casefold()),
            None,
        )
        day_match = re.search(r"第([一二两三四五六七\d]+)天", text)
        target_date = None
        if day_match:
            token = day_match.group(1)
            index = int(token) if token.isdigit() else _CHINESE_NUMBERS.get(token)
            if index:
                target_date = request.trip_start.fromordinal(
                    request.trip_start.toordinal() + index - 1
                )
        if "换" in text or "替代" in text:
            parts = re.split(r"换成|替换成|替代为", text, maxsplit=1)
            poi_name = parts[-1].strip("。 ，,") if len(parts) == 2 else ""
            operation = EditOperation(
                kind=EditOperationKind.REPLACE_ITEM,
                item_id=matched.item_id if matched else None,
                item_name=matched.name if matched else "待替换项目",
                poi_name=poi_name or "替代景点",
            )
        elif "删除" in text or "去掉" in text or "移除" in text:
            operation = EditOperation(
                kind=EditOperationKind.REMOVE_ITEM,
                item_id=matched.item_id if matched else None,
                item_name=matched.name if matched else "待删除项目",
            )
        elif "移" in text or "挪" in text:
            operation = EditOperation(
                kind=EditOperationKind.MOVE_ITEM,
                item_id=matched.item_id if matched else None,
                item_name=matched.name if matched else "待移动项目",
                target_date=target_date or request.trip_end,
                target_index=0,
            )
        else:
            operation = EditOperation(
                kind=EditOperationKind.REORDER_ITEM,
                item_id=matched.item_id if matched else None,
                item_name=matched.name if matched else "待调整项目",
                target_index=0,
            )
        return EditProviderOutput(patch=EditPatch(operations=(operation,)))

