import copy

import pytest

from casedesk.fixtures import TICKETS
from casedesk.policy import decide


def ticket():
    return {**copy.deepcopy(TICKETS[0]), "status": "open"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("days_since_purchase", 31),
        ("days_since_purchase", -1),
        ("amount_cents", 0),
        ("amount_cents", 20001),
        ("delivered", False),
        ("refunded", True),
    ],
)
def test_refund_eligibility_uses_server_order_facts(field, value):
    data = ticket()
    data["order"][field] = value
    verdict = decide(data, "refund", has_evidence=True)
    assert not verdict["allowed"]
    assert verdict["normalized_parameters"] == {}


def test_refund_eligible_boundary_and_fixed_server_amount():
    data = ticket()
    data["order"].update(days_since_purchase=30, amount_cents=20000)
    verdict = decide(data, "refund", has_evidence=True)
    assert verdict["allowed"]
    assert verdict["normalized_parameters"] == {"order_id": "ORDER-01", "amount_cents": 20000}


@pytest.mark.parametrize("reason", ["unverified", "no_evidence", "closed"])
def test_sensitive_actions_need_verified_account_evidence_open_ticket(reason):
    data = ticket()
    if reason == "unverified":
        data["account"]["verified"] = False
    elif reason == "closed":
        data["status"] = "closed"
    assert not decide(data, "refund", has_evidence=reason != "no_evidence")["allowed"]


def test_coupon_uses_fixed_amount_and_no_repeat():
    data = ticket()
    assert not decide(data, "coupon", has_evidence=True)["allowed"]
    data["order"]["delivery_delay_days"] = 3
    verdict = decide(data, "coupon", has_evidence=True)
    assert verdict["allowed"]
    assert verdict["normalized_parameters"]["amount_cents"] == 500
    assert not decide(data, "coupon", has_evidence=True, coupon_exists=True)["allowed"]


def test_safe_abstention_and_escalation_do_not_request_credentials():
    data = ticket()
    data["account"]["verified"] = False
    verdict = decide(data, "request_info", has_evidence=False)
    assert verdict["allowed"]
    assert verdict["normalized_parameters"]["fields"] == ["order_id", "account_verification"]
    data["category"] = "account"
    assert decide(data, "escalate", has_evidence=False)["normalized_parameters"] == {
        "queue": "security"
    }
    assert not decide(data, "execute_shell", has_evidence=True)["allowed"]


@pytest.mark.parametrize("action", ["refund", "coupon"])
def test_account_security_category_cannot_apply_financial_action(action):
    data = ticket()
    data["category"] = "account"
    data["order"]["delivery_delay_days"] = 4
    assert not decide(data, action, has_evidence=True)["allowed"]
