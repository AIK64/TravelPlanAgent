from __future__ import annotations

from datetime import date

from travel_agent.domain.lifecycle_models import EditOperation, EditPatch
from travel_agent.domain.models import PlanCandidate, PlanningPOI, TripSpec
from travel_agent.lifecycle.errors import LifecycleActionError
from travel_agent.planning.drafts import CandidateDraft, DraftDay


def edit_item_context(candidate: PlanCandidate) -> tuple[dict, ...]:
    return tuple(
        {
            "item_id": item.item_id,
            "poi_id": item.poi_id,
            "name": item.name,
            "day": day.date,
            "index": index,
            "locked": item.locked,
        }
        for day in candidate.days
        for index, item in enumerate(day.items)
        if item.item_id is not None
    )


def ground_edit_patch(
    session_id: str,
    candidate: PlanCandidate,
    patch: EditPatch,
) -> EditPatch:
    items = [
        (day.date, item)
        for day in candidate.days
        for item in day.items
        if item.item_id is not None
    ]
    ids = {item.item_id for _, item in items}
    grounded = []
    for operation in patch.operations:
        item_id = operation.item_id
        if item_id is not None and item_id not in ids:
            raise LifecycleActionError(session_id, "unknown_item", "编辑引用的计划项目不存在")
        if item_id is None and operation.item_name:
            needle = operation.item_name.strip().casefold()
            matches = [
                item
                for _, item in items
                if needle in item.name.strip().casefold()
                or item.name.strip().casefold() in needle
            ]
            if len(matches) != 1:
                code = "ambiguous_item" if len(matches) > 1 else "unknown_item"
                raise LifecycleActionError(
                    session_id,
                    code,
                    "无法唯一确定要修改的计划项目，请使用响应中的 item_id",
                )
            item_id = matches[0].item_id
        grounded.append(operation.model_copy(update={"item_id": item_id}))
    return EditPatch(operations=tuple(grounded))


def _is_must_visit(name: str, trip: TripSpec) -> bool:
    normalized = name.strip().casefold()
    return any(
        required.strip().casefold() in normalized
        or normalized in required.strip().casefold()
        for required in trip.must_visit
    )


def apply_edit_patch(
    *,
    session_id: str,
    request_id: str,
    trip: TripSpec,
    candidate: PlanCandidate,
    draft: CandidateDraft,
    planning_pois: tuple[PlanningPOI, ...],
    patch: EditPatch,
) -> tuple[CandidateDraft, dict[str, str]]:
    """把已 Ground 的白名单动作应用到 Draft；返回 POI 到稳定 item ID 映射。"""
    days = {day.date: list(day.poi_ids) for day in draft.days}
    candidate_items = {
        item.item_id: (day.date, item)
        for day in candidate.days
        for item in day.items
        if item.item_id is not None
    }
    item_ids_by_poi = {
        item.poi_id: item.item_id
        for _, item in candidate_items.values()
        if item.poi_id is not None and item.item_id is not None
    }
    pois_by_id = {poi.facts.id: poi for poi in planning_pois}

    def resolve_poi(name: str) -> str:
        needle = name.strip().casefold()
        matches = [
            poi.facts.id
            for poi in planning_pois
            if needle in poi.facts.name.strip().casefold()
            or poi.facts.name.strip().casefold() in needle
        ]
        if len(matches) != 1:
            raise LifecycleActionError(
                session_id,
                "poi_not_in_planning_context",
                "替代地点无法从当前标准化 POI 中唯一解析，请创建新计划",
            )
        return matches[0]

    for index, operation in enumerate(patch.operations):
        item_entry = candidate_items.get(operation.item_id) if operation.item_id else None
        source_day: date | None = item_entry[0] if item_entry else None
        source_item = item_entry[1] if item_entry else None
        if source_item is not None and source_item.poi_id is None:
            raise LifecycleActionError(session_id, "unsupported_item", "当前只支持编辑 POI 活动")
        if source_item is not None and _is_must_visit(source_item.name, trip) and operation.kind.value in {"remove_item", "replace_item"}:
            raise LifecycleActionError(session_id, "must_visit_protected", "必去项目不能删除或替换")

        if operation.kind.value == "remove_item":
            assert source_day is not None and source_item is not None and source_item.poi_id
            days[source_day].remove(source_item.poi_id)
        elif operation.kind.value in {"move_item", "reorder_item"}:
            assert source_day is not None and source_item is not None and source_item.poi_id
            days[source_day].remove(source_item.poi_id)
            target_day = operation.target_date or source_day
            if target_day not in days:
                raise LifecycleActionError(session_id, "date_out_of_range", "目标日期不在旅行范围内")
            target_index = operation.target_index
            if target_index is None:
                target_index = len(days[target_day])
            days[target_day].insert(min(target_index, len(days[target_day])), source_item.poi_id)
        elif operation.kind.value == "replace_item":
            assert source_day is not None and source_item is not None and source_item.poi_id
            new_poi_id = resolve_poi(operation.poi_name or "")
            position = days[source_day].index(source_item.poi_id)
            days[source_day][position] = new_poi_id
            from travel_agent.lifecycle.fingerprints import new_item_id
            item_ids_by_poi[new_poi_id] = new_item_id(session_id, request_id, index)
        elif operation.kind.value == "add_item":
            assert operation.target_date is not None
            if operation.target_date not in days:
                raise LifecycleActionError(session_id, "date_out_of_range", "目标日期不在旅行范围内")
            new_poi_id = resolve_poi(operation.poi_name or "")
            if new_poi_id not in pois_by_id:
                raise LifecycleActionError(session_id, "unknown_poi", "替代地点不存在")
            target_index = operation.target_index
            if target_index is None:
                target_index = len(days[operation.target_date])
            days[operation.target_date].insert(
                min(target_index, len(days[operation.target_date])), new_poi_id
            )
            from travel_agent.lifecycle.fingerprints import new_item_id
            item_ids_by_poi[new_poi_id] = new_item_id(session_id, request_id, index)

    return (
        CandidateDraft(
            id=draft.id,
            style=draft.style,
            days=tuple(
                DraftDay(date=day.date, poi_ids=tuple(days[day.date]))
                for day in draft.days
            ),
        ),
        {key: value for key, value in item_ids_by_poi.items() if key and value},
    )

