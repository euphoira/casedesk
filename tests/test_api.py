import pytest
from fastapi.testclient import TestClient

from casedesk.api import create_app
from casedesk.config import Settings


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(Settings(data_dir=tmp_path)), raise_server_exceptions=False
    ) as value:
        yield value


def test_full_human_approval_and_idempotent_retry(client):
    config = client.get("/api/config").json()
    assert config["simulation"] and config["mode"] == "demo"
    assert config["model"] == "deterministic-fixtures"
    assert "api_key" not in config and "base_url" not in config
    result = client.post("/api/tickets/CS-1001/analyze").json()
    proposal = result["proposal"]
    assert proposal["status"] == "pending"
    assert client.app.state.store.effects() == []
    payload = {"ticket_revision": 1, "idempotency_key": "ui-approval-123"}
    first = client.post(f"/api/proposals/{proposal['id']}/approve", json=payload)
    assert first.status_code == 200
    repeated = client.post(f"/api/proposals/{proposal['id']}/approve", json=payload)
    assert repeated.json()["replayed"]
    assert repeated.json()["effect"]["id"] == first.json()["effect"]["id"]
    assert client.get("/api/tickets/CS-1001").json()["revision"] == 2
    assert client.get("/api/tickets/CS-1001/analysis").json()["proposal"]["status"] == "approved"
    assert any(
        e["event"] == "simulation_applied" for e in client.get("/api/tickets/CS-1001/audit").json()
    )


def test_intake_context_is_server_owned_and_shared_order_facts_are_preserved(client):
    contexts = client.get("/api/contexts").json()
    assert len(contexts) == 5
    response = client.post(
        "/api/tickets",
        json={
            "subject": "新建退款工单",
            "body": "这个订单需要核实退款资格，请帮助检查。",
            "category": "billing",
            "context_id": "ORDER-01",
        },
    )
    assert response.status_code == 201
    ticket = response.json()
    assert ticket["order"]["amount_cents"] == 5900 and ticket["revision"] == 1
    assert client.get(f"/api/tickets/{ticket['id']}").json() == ticket


@pytest.mark.parametrize(
    "payload",
    [
        {"subject": "too", "body": "too short", "category": "billing", "context_id": "ORDER-01"},
        {
            "subject": "valid title",
            "body": "a valid ticket description",
            "category": "billing",
            "context_id": "unknown",
        },
        {
            "subject": "valid title",
            "body": "a valid ticket description",
            "category": "billing",
            "context_id": "ORDER-01",
            "amount_cents": 1,
        },
    ],
)
def test_intake_rejects_bad_or_unowned_fields(client, payload):
    assert client.post("/api/tickets", json=payload).status_code == 422


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "http://[", "null", "http://testserver/path"]
)
def test_write_origin_protection(client, origin):
    assert (
        client.post("/api/tickets/CS-1001/analyze", headers={"Origin": origin}).status_code == 403
    )


def test_host_and_actual_body_bounds(client):
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/api/tickets/CS-1001/analyze", headers={"Host": "["}).status_code in {
        400,
        403,
    }
    response = client.post("/api/tickets/CS-1001/analyze", content=iter([b"x" * 16001]))
    assert response.status_code == 413
    assert client.get("/health").headers["x-content-type-options"] == "nosniff"


def test_approve_payload_cannot_override_amount_or_revision_type(client):
    proposal = client.post("/api/tickets/CS-1001/analyze").json()["proposal"]
    url = f"/api/proposals/{proposal['id']}/approve"
    assert (
        client.post(
            url,
            json={"ticket_revision": 1, "idempotency_key": "approval-key", "amount_cents": 999999},
        ).status_code
        == 422
    )
    assert (
        client.post(
            url, json={"ticket_revision": True, "idempotency_key": "approval-key"}
        ).status_code
        == 422
    )
    assert client.app.state.store.effects() == []


def test_policy_denial_rejection_and_stale_conflict(client):
    denied = client.post("/api/tickets/CS-1002/analyze").json()["proposal"]
    assert (
        client.post(
            f"/api/proposals/{denied['id']}/approve",
            json={"ticket_revision": 1, "idempotency_key": "denied-key-1"},
        ).status_code
        == 403
    )
    assert (
        client.post(f"/api/proposals/{denied['id']}/reject", json={"ticket_revision": 1}).json()[
            "status"
        ]
        == "rejected"
    )
    proposal = client.post("/api/tickets/CS-1001/analyze").json()["proposal"]
    assert (
        client.patch(
            "/api/tickets/CS-1001", json={"expected_revision": 1, "status": "escalated"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/proposals/{proposal['id']}/approve",
            json={"ticket_revision": 1, "idempotency_key": "stale-key-1"},
        ).status_code
        == 409
    )


def test_coupon_scenario_and_duplicate_order_coupon(client):
    proposal = client.post("/api/tickets/CS-1005/analyze").json()["proposal"]
    assert proposal["action"] == "coupon" and proposal["policy"]["allowed"]
    effect = client.post(
        f"/api/proposals/{proposal['id']}/approve",
        json={"ticket_revision": 1, "idempotency_key": "coupon-approval-key"},
    ).json()["effect"]
    assert effect["parameters"]["amount_cents"] == 500
    second = client.post("/api/tickets/CS-1005/analyze").json()["proposal"]
    assert not second["policy"]["allowed"]


def test_search_and_unknown_resources(client):
    assert client.get("/api/search", params={"q": "refund"}).json()["chunks"]
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/tickets/absent").status_code == 404
    assert client.get("/api/tickets/CS-1001/analysis").status_code == 404
    assert client.post("/api/tickets/absent/analyze").status_code == 404
    assert (
        client.post(
            "/api/proposals/absent/approve",
            json={"ticket_revision": 1, "idempotency_key": "unknown-key"},
        ).status_code
        == 404
    )
