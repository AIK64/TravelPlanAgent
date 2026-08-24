from __future__ import annotations

from copy import deepcopy
from uuid import uuid4


COMPLETE_TEXT = (
    "2026年10月2日到10月4日去杭州，3个人，预算1500元，住西湖东侧，"
    "喜欢自然和美食，2日10:30到杭州东站，4日19:00从杭州东站离开，"
    "灵隐寺必须去，不想太累。"
)


def _create(client):
    response = client.post(
        "/api/v1/plan-sessions/from-text",
        json={"text": COMPLETE_TEXT, "reference_date": "2026-08-23"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "awaiting_candidate_selection"
    assert body["interrupt"]["payload"]["kind"] == "candidate_selection"
    return body


def _resume(client, session, body, action, *, request_id=None):
    return client.post(
        f"/api/v1/plan-sessions/{session['session_id']}/resume",
        json={
            "interrupt_id": session["interrupt"]["id"],
            "request_id": request_id or str(uuid4()),
            "expected_active_version_id": body.get("active_version_id"),
            "expected_session_revision": body.get("revision"),
            "action": action,
        },
    )


def test_select_lock_local_edit_approve_and_diff(client):
    created = _create(client)
    selected_response = _resume(
        client,
        created,
        {"revision": 0},
        {"kind": "accept_recommendation"},
    )
    assert selected_response.status_code == 200
    selected = selected_response.json()
    assert selected["active_version"]["version_id"] == "V1"
    assert selected["interrupt"]["payload"]["kind"] == "plan_change"
    baseline_day_2 = deepcopy(selected["active_version"]["candidate"]["days"][1])

    locked_response = _resume(
        client,
        selected,
        {"active_version_id": "V1", "revision": 1},
        {"kind": "lock", "lock_kind": "day", "target_id": "2026-10-03"},
    )
    assert locked_response.status_code == 200
    locked = locked_response.json()
    assert locked["session_revision"] == 2
    assert locked["locks"][0]["target_id"] == "2026-10-03"

    source_item = locked["active_version"]["candidate"]["days"][0]["items"][0]
    preview_response = _resume(
        client,
        locked,
        {"active_version_id": "V1", "revision": 2},
        {
            "kind": "edit",
            "patch": {
                "operations": [
                    {
                        "kind": "move_item",
                        "item_id": source_item["item_id"],
                        "target_date": "2026-10-04",
                        "target_index": 0,
                    }
                ]
            },
        },
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["status"] == "awaiting_change_approval"
    assert preview["pending_preview"]["diff"]["moved_items"][0]["item_id"] == source_item["item_id"]
    assert preview["pending_preview"]["candidate"]["days"][1] == baseline_day_2
    preview_diff = client.get(
        f"/api/v1/plan-sessions/{created['session_id']}/diff",
        params={"from_id": "V1", "to_id": preview["pending_preview"]["preview_id"]},
    )
    assert preview_diff.status_code == 200
    missing_diff = client.get(
        f"/api/v1/plan-sessions/{created['session_id']}/diff",
        params={"from_id": "V1", "to_id": "P99"},
    )
    assert missing_diff.status_code == 422

    approved_response = _resume(
        client,
        preview,
        {"active_version_id": "V1", "revision": 3},
        {
            "kind": "approve_preview",
            "preview_id": preview["pending_preview"]["preview_id"],
            "approval_token": preview["interrupt"]["payload"]["approval_token"],
        },
    )
    assert approved_response.status_code == 200
    approved = approved_response.json()
    assert approved["active_version"]["version_id"] == "V2"
    assert approved["active_version"]["parent_version_id"] == "V1"
    assert approved["active_version"]["candidate"]["days"][1] == baseline_day_2

    diff = client.get(
        f"/api/v1/plan-sessions/{created['session_id']}/diff",
        params={"from_id": "V1", "to_id": "V2"},
    )
    assert diff.status_code == 200
    assert diff.json()["to_id"] == "V2"


def test_lock_guard_rejects_before_preview(client):
    created = _create(client)
    selected = _resume(
        client, created, {"revision": 0}, {"kind": "accept_recommendation"}
    ).json()
    item = selected["active_version"]["candidate"]["days"][0]["items"][0]
    locked = _resume(
        client,
        selected,
        {"active_version_id": "V1", "revision": 1},
        {"kind": "lock", "lock_kind": "item", "target_id": item["item_id"]},
    ).json()
    rejected = _resume(
        client,
        locked,
        {"active_version_id": "V1", "revision": 2},
        {
            "kind": "edit",
            "patch": {"operations": [{"kind": "remove_item", "item_id": item["item_id"]}]},
        },
    )

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "lock_conflict"
    current = client.get(f"/api/v1/plan-sessions/{created['session_id']}").json()
    assert current["active_version"]["version_id"] == "V1"
    assert current["pending_preview"] is None


def test_alternative_selection_unlock_reject_and_stale_interrupt(client):
    created = _create(client)
    recommended = created["interrupt"]["payload"]["recommended_candidate_id"]
    alternative = next(item["id"] for item in created["candidates"] if item["id"] != recommended)
    selected = _resume(
        client,
        created,
        {"revision": 0},
        {"kind": "select_candidate", "candidate_id": alternative},
    ).json()
    assert selected["active_version"]["selected_candidate_id"] == alternative

    stale = _resume(
        client,
        created,
        {"revision": 0},
        {"kind": "select_candidate", "candidate_id": alternative},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_interrupt"

    locked = _resume(
        client,
        selected,
        {"active_version_id": "V1", "revision": 1},
        {"kind": "lock", "lock_kind": "day", "target_id": "2026-10-03"},
    ).json()
    unlocked = _resume(
        client,
        locked,
        {"active_version_id": "V1", "revision": 2},
        {"kind": "unlock", "lock_kind": "day", "target_id": "2026-10-03"},
    ).json()
    assert unlocked["locks"] == []

    item = unlocked["active_version"]["candidate"]["days"][0]["items"][-1]
    preview = _resume(
        client,
        unlocked,
        {"active_version_id": "V1", "revision": 3},
        {
            "kind": "edit_text",
            "text": f"把{item['name']}挪到第三天",
        },
    ).json()
    assert preview["status"] == "awaiting_change_approval"
    rejected = _resume(
        client,
        preview,
        {"active_version_id": "V1", "revision": 4},
        {
            "kind": "reject_preview",
            "preview_id": preview["pending_preview"]["preview_id"],
        },
    ).json()
    assert rejected["status"] == "active"
    assert rejected["active_version"]["version_id"] == "V1"
    assert rejected["pending_preview"]["status"] == "rejected"


def test_selection_request_is_idempotent(client):
    created = _create(client)
    request_id = str(uuid4())
    first = _resume(
        client,
        created,
        {"revision": 0},
        {"kind": "accept_recommendation"},
        request_id=request_id,
    )
    second = _resume(
        client,
        created,
        {"revision": 0},
        {"kind": "accept_recommendation"},
        request_id=request_id,
    )

    assert first.status_code == second.status_code == 200
    assert second.json()["session_revision"] == 1
    versions = client.get(
        f"/api/v1/plan-sessions/{created['session_id']}/versions"
    ).json()
    assert [item["version_id"] for item in versions] == ["V1"]


def test_ambiguous_edit_interrupt_can_be_grounded_by_item_id(client):
    created = _create(client)
    selected = _resume(
        client, created, {"revision": 0}, {"kind": "accept_recommendation"}
    ).json()
    item = selected["active_version"]["candidate"]["days"][0]["items"][0]
    interrupted = _resume(
        client,
        selected,
        {"active_version_id": "V1", "revision": 1},
        {
            "kind": "edit",
            "patch": {
                "operations": [
                    {"kind": "move_item", "item_name": "不明确的地点", "target_date": "2026-10-04"}
                ]
            },
        },
    )
    assert interrupted.status_code == 200
    body = interrupted.json()
    assert body["status"] == "needs_edit_clarification"
    assert body["interrupt"]["payload"]["kind"] == "edit_clarification"

    clarified = _resume(
        client,
        body,
        {"active_version_id": "V1", "revision": 2},
        {"kind": "clarify_edit", "item_id": item["item_id"]},
    )
    assert clarified.status_code == 200
    assert clarified.json()["status"] == "awaiting_change_approval"


def test_natural_session_reuses_requirement_clarification_loop(client):
    created = client.post(
        "/api/v1/plan-sessions/from-text",
        json={
            "text": (
                "2026年10月2日到10月4日去杭州，3个人，预算1500元，住西湖东侧，"
                "喜欢自然和美食，2日10:30到杭州东站，灵隐寺必须去，不想太累。"
            ),
            "reference_date": "2026-08-23",
        },
    ).json()
    assert created["status"] == "needs_requirement_clarification"
    assert created["interrupt"]["payload"]["kind"] == "requirement_clarification"

    resumed = _resume(
        client,
        created,
        {"revision": 0},
        {"kind": "clarify_requirement", "answer": "10月4日19:00从杭州东站离开。"},
    )
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["status"] == "awaiting_candidate_selection"
    assert body["interrupt"]["payload"]["kind"] == "candidate_selection"


def test_structured_session_query_and_not_found_contracts(client, hangzhou_trip):
    created_response = client.post(
        "/api/v1/plan-sessions",
        json={"trip": hangzhou_trip.model_dump(mode="json")},
    )
    assert created_response.status_code == 200
    created = created_response.json()
    fetched = client.get(
        f"/api/v1/plan-sessions/{created['session_id']}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["interrupt"]["id"] == created["interrupt"]["id"]

    selected = _resume(
        client, created, {"revision": 0}, {"kind": "accept_recommendation"}
    ).json()
    version = client.get(
        f"/api/v1/plan-sessions/{created['session_id']}/versions/V1"
    )
    assert version.status_code == 200
    assert version.json()["plan_fingerprint"] == selected["active_version"]["plan_fingerprint"]
    missing_version = client.get(
        f"/api/v1/plan-sessions/{created['session_id']}/versions/V99"
    )
    assert missing_version.status_code == 422
    missing_session = client.get("/api/v1/plan-sessions/missing")
    assert missing_session.status_code == 404


def test_wide_edit_requires_new_plan_without_tool_preview(client):
    created = _create(client)
    selected = _resume(
        client, created, {"revision": 0}, {"kind": "accept_recommendation"}
    ).json()
    operations = []
    for day in selected["active_version"]["candidate"]["days"]:
        operations.append(
            {
                "kind": "reorder_item",
                "item_id": day["items"][0]["item_id"],
                "target_index": 0,
            }
        )
    response = _resume(
        client,
        selected,
        {"active_version_id": "V1", "revision": 1},
        {"kind": "edit", "patch": {"operations": operations}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "requires_new_plan"
    assert body["pending_preview"] is None


def test_bad_approval_token_does_not_commit_version(client):
    created = _create(client)
    selected = _resume(
        client, created, {"revision": 0}, {"kind": "accept_recommendation"}
    ).json()
    item = selected["active_version"]["candidate"]["days"][0]["items"][0]
    preview = _resume(
        client,
        selected,
        {"active_version_id": "V1", "revision": 1},
        {
            "kind": "edit",
            "patch": {
                "operations": [
                    {
                        "kind": "move_item",
                        "item_id": item["item_id"],
                        "target_date": "2026-10-04",
                    }
                ]
            },
        },
    ).json()
    rejected = _resume(
        client,
        preview,
        {"active_version_id": "V1", "revision": 2},
        {
            "kind": "approve_preview",
            "preview_id": preview["pending_preview"]["preview_id"],
            "approval_token": "invalid-token-value",
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "stale_approval_token"
    versions = client.get(
        f"/api/v1/plan-sessions/{created['session_id']}/versions"
    ).json()
    assert [item["version_id"] for item in versions] == ["V1"]
