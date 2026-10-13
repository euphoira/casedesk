import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor

import pytest

from casedesk import policy
from casedesk.retrieval import KnowledgeBase
from casedesk.store import Conflict, Denied, Store


@pytest.fixture
def proposal(setup):
    _, store, kb, engine = setup
    analysis = asyncio.run(engine.analyze("CS-1001"))
    assert analysis["status"] == "completed", analysis
    return store, kb, engine, analysis["proposal"]


def test_analysis_never_executes_and_approval_is_durable(proposal):
    store, kb, _, item = proposal
    assert store.effects() == []
    result = store.approve(item["id"], 1, "human-approval-1", kb)
    assert result["effect"]["simulation"] is True
    assert result["effect"]["parameters"]["amount_cents"] == 5900
    assert result["proposal"]["status"] == "approved"
    fresh = Store(store.path)
    assert len(fresh.effects()) == 1
    assert fresh.ticket("CS-1001")["revision"] == 2
    assert fresh.ticket("CS-1001")["order"]["refunded"]
    assert any(event["event"] == "approval_policy_checked" for event in fresh.audit("CS-1001"))


def test_retry_same_idempotency_key_replays_original_effect(proposal):
    store, kb, _, item = proposal
    first = store.approve(item["id"], 1, "stable-approval-key", kb)
    replay = store.approve(item["id"], 1, "stable-approval-key", kb)
    assert not first["replayed"] and replay["replayed"]
    assert first["effect"] == replay["effect"]
    assert len(store.effects()) == 1
    with pytest.raises(Conflict):
        store.approve(item["id"], 1, "different-key", kb)


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_approvals_never_duplicate_effect(proposal, same_key):
    store, kb, _, item = proposal

    def approve(index):
        try:
            return store.approve(
                item["id"], 1, "concurrent-key" if same_key else f"different-key-{index}", kb
            )
        except Conflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(approve, range(2)))
    assert len(store.effects()) == 1
    assert sum(result is not None for result in results) == (2 if same_key else 1)
    if same_key:
        assert sum(result["replayed"] for result in results) == 1


def test_key_cannot_be_reused_across_proposals(proposal):
    store, kb, engine, item = proposal
    other = asyncio.run(engine.analyze("CS-1003"))["proposal"]
    store.approve(item["id"], 1, "single-purpose-key", kb)
    with pytest.raises(Conflict, match="幂等"):
        store.approve(other["id"], 1, "single-purpose-key", kb)
    assert len(store.effects()) == 1


@pytest.mark.parametrize("stale", ["ticket", "corpus", "policy", "analysis"])
def test_stale_approvals_are_rejected_and_audited(proposal, monkeypatch, stale):
    store, kb, engine, item = proposal
    if stale == "ticket":
        store.update_ticket("CS-1001", 1, "escalated")
    elif stale == "corpus":
        kb = KnowledgeBase({"revision": "new-kb", "chunks": list(kb.chunks.values())})
    elif stale == "policy":
        monkeypatch.setattr(policy, "POLICY_REVISION", "new-policy")
    else:
        asyncio.run(engine.analyze("CS-1001"))
    with pytest.raises(Conflict):
        store.approve(item["id"], 1, "stale-approval-key", kb)
    assert store.effects() == []
    assert store.audit("CS-1001")[-1]["event"] == "approval_denied"


def test_reject_is_terminal_and_never_executes(proposal):
    store, kb, _, item = proposal
    assert store.reject(item["id"], 1)["status"] == "rejected"
    with pytest.raises(Conflict):
        store.approve(item["id"], 1, "rejected-action-key", kb)
    assert store.effects() == []


def test_expired_refund_denial_is_audited(setup):
    _, store, kb, engine = setup
    item = asyncio.run(engine.analyze("CS-1002"))["proposal"]
    assert not item["policy"]["allowed"]
    with pytest.raises(Denied):
        store.approve(item["id"], 1, "expired-refund-key", kb)
    assert store.effects() == []
    assert store.audit("CS-1002")[-1]["event"] == "approval_denied"


def test_effect_uses_policy_amount_not_mutated_proposal_parameters(proposal):
    store, kb, _, item = proposal
    corrupted = copy.deepcopy(item)
    corrupted["parameters"] = {"amount_cents": 99999999, "send_to": "attacker"}
    with store.connection() as conn:
        store._save(conn, "proposals", corrupted)
    result = store.approve(item["id"], 1, "normalized-effect-key", kb)
    assert result["effect"]["parameters"] == {"order_id": "ORDER-01", "amount_cents": 5900}


def test_shared_order_across_tickets_cannot_refund_twice(proposal):
    store, kb, engine, item = proposal
    other = store.create_ticket(
        "同一订单再次申请", "同一订单希望退回已付款项，请核查。", "billing", "ORDER-01"
    )
    second = asyncio.run(engine.analyze(other["id"]))["proposal"]
    assert second["policy"]["allowed"]
    store.approve(item["id"], 1, "first-ticket-refund", kb)
    with pytest.raises(Denied):
        store.approve(second["id"], 1, "second-ticket-refund", kb)
    assert len(store.effects()) == 1
    assert store.contexts()[0]["order"]["refunded"]
    assert store.ticket(other["id"])["order"]["refunded"]
    assert next(t for t in store.tickets() if t["id"] == other["id"])["order"]["refunded"]
    refreshed = asyncio.run(engine.analyze(other["id"]))["proposal"]
    assert not refreshed["policy"]["allowed"]


def test_ticket_update_optimistic_lock(proposal):
    store, _, _, _ = proposal
    store.update_ticket("CS-1001", 1, "escalated")
    with pytest.raises(Conflict):
        store.update_ticket("CS-1001", 1, "closed")
    assert store.ticket("CS-1001")["status"] == "escalated"
