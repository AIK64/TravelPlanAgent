from __future__ import annotations

from fastapi.testclient import TestClient

from travel_agent.app import app


client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_plan(hangzhou_trip):
    response = client.post(
        "/api/v1/plans",
        json={
            "trip": hangzhou_trip.model_dump(mode="json"),
            "max_replan_rounds": 2,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["selected_plan"]["validation"]["valid"] is True


def test_json_response_declares_utf8_for_legacy_windows_clients(hangzhou_trip):
    response = client.post(
        "/api/v1/plans",
        json={
            "trip": hangzhou_trip.model_dump(mode="json"),
            "max_replan_rounds": 2,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].lower() == "application/json; charset=utf-8"
