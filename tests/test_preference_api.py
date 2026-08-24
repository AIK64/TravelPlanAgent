from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from travel_agent.app import create_app
from travel_agent.config import Settings


HEADERS_A = {"X-Tenant-Id": "tenant-a", "X-User-Id": "user-a"}
HEADERS_B = {"X-Tenant-Id": "tenant-a", "X-User-Id": "user-b"}


def test_preference_crud_isolation_and_personalization() -> None:
    application = create_app(Settings.from_env({}))
    with TestClient(application) as client:
        created = client.post(
            "/api/v1/preferences",
            headers=HEADERS_A,
            json={"category": "pace", "value": "relaxed"},
        )
        assert created.status_code == 201
        memory = created.json()

        own = client.get("/api/v1/preferences", headers=HEADERS_A)
        other = client.get("/api/v1/preferences", headers=HEADERS_B)
        assert own.status_code == 200
        assert len(own.json()["items"]) == 1
        assert other.json()["items"] == []

        hidden = client.patch(
            f"/api/v1/preferences/{memory['memory_id']}",
            headers=HEADERS_B,
            json={"value": "balanced", "expected_revision": 1},
        )
        assert hidden.status_code == 404

        updated = client.patch(
            f"/api/v1/preferences/{memory['memory_id']}",
            headers=HEADERS_A,
            json={"value": "balanced", "expected_revision": 1},
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == 2

        settings = client.patch(
            "/api/v1/profile/personalization",
            headers=HEADERS_A,
            json={"enabled": False, "expected_revision": 1},
        )
        assert settings.status_code == 200
        assert settings.json()["enabled"] is False

        exported = client.get("/api/v1/preferences/export", headers=HEADERS_A)
        assert exported.status_code == 200
        assert len(exported.json()["items"]) == 1


def test_memory_proposal_confirmation_is_idempotent() -> None:
    application = create_app(Settings.from_env({}))
    with TestClient(application) as client:
        proposal = client.post(
            "/api/v1/preferences/proposals",
            headers=HEADERS_A,
            json={
                "category": "preferred_categories",
                "value": ["自然", "美食"],
                "source": "model_inference",
                "confidence": 0.7,
                "reason": "跨旅行重复选择",
            },
        )
        assert proposal.status_code == 201
        proposal_id = proposal.json()["proposal_id"]
        request_id = str(uuid4())

        first = client.post(
            f"/api/v1/preferences/proposals/{proposal_id}/confirm",
            headers=HEADERS_A,
            json={"request_id": request_id},
        )
        replay = client.post(
            f"/api/v1/preferences/proposals/{proposal_id}/confirm",
            headers=HEADERS_A,
            json={"request_id": request_id},
        )
        assert first.status_code == 200
        assert replay.status_code == 200
        assert first.json()["memory_id"] == replay.json()["memory_id"]


def test_cross_session_personalization_is_visible_in_planning_response() -> None:
    application = create_app(Settings.from_env({}))
    with TestClient(application) as client:
        stored = client.post(
            "/api/v1/preferences",
            headers=HEADERS_A,
            json={"category": "pace", "value": "relaxed"},
        )
        assert stored.status_code == 201

        planned = client.post(
            "/api/v1/plans/from-text",
            headers=HEADERS_A,
            json={
                "text": (
                    "2026年10月2日到10月4日去杭州，2个人，预算2000元，"
                    "住西湖东侧，2日10:30到杭州东站，"
                    "4日19:00从杭州东站离开，灵隐寺必须去。"
                ),
                "reference_date": "2026-08-24",
            },
        )
        assert planned.status_code == 200
        payload = planned.json()
        assert payload["trip"]["pace"] == "relaxed"
        assert payload["personalized_fields"] == ["pace"]
        assert payload["preference_context"]["manifest"]["selected_memory_ids"]


def test_identity_is_required_when_dev_identity_disabled() -> None:
    application = create_app(
        Settings.from_env({"DEV_IDENTITY_ENABLED": "false"})
    )
    with TestClient(application) as client:
        response = client.get("/api/v1/preferences")
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "authentication_required"
