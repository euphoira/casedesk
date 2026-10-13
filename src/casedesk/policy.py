"""Business authorization uses only server-owned facts, never model calculations."""

POLICY_REVISION = "policy-2026-01"


def decide(ticket: dict, action: str, *, has_evidence: bool, coupon_exists: bool = False) -> dict:
    allowed, reason, parameters = False, "未知操作。", {}
    account, order = ticket["account"], ticket["order"]
    if ticket["status"] == "closed":
        reason = "工单已关闭，禁止继续执行。"
    elif action == "request_info":
        allowed = True
        reason = "允许请求最小必要信息；仅保存本地模拟请求。"
        parameters = {"fields": ["order_id", "account_verification"], "channel": "local_simulation"}
    elif action == "escalate":
        allowed = True
        reason = "允许升级人工专员；仅保存本地模拟升级记录。"
        parameters = {"queue": "security" if ticket["category"] == "account" else "support"}
    elif action in {"refund", "coupon"}:
        if ticket["category"] == "account":
            reason = "账户安全类工单必须先升级人工专员，不能执行金额操作。"
        elif not has_evidence:
            reason = "金额操作必须具有已检索且可核验的政策引用。"
        elif not account["verified"]:
            reason = "账户未验证，必须先补充信息。"
        elif order["refunded"]:
            reason = "该订单已退款，不能再次退款或发放优惠券。"
        elif action == "refund":
            if not order["delivered"]:
                reason = "订单尚未送达，不符合退款演示政策。"
            elif not 0 <= order["days_since_purchase"] <= 30:
                reason = "已超过 30 天退款窗口。"
            elif not 0 < order["amount_cents"] <= 20_000:
                reason = "订单金额超出单笔 20000 分的自动审批上限。"
            else:
                allowed, reason = True, "账户、送达状态、购买天数和金额均符合退款政策。"
                parameters = {"order_id": ticket["order_id"], "amount_cents": order["amount_cents"]}
        elif coupon_exists:
            reason = "该订单已经发放过模拟优惠券。"
        elif order.get("delivery_delay_days", 0) < 3:
            reason = "配送延误不足 3 天，不符合优惠券政策。"
        else:
            allowed, reason = True, "延误达到 3 天，允许发放一次固定额度优惠券。"
            parameters = {"order_id": ticket["order_id"], "amount_cents": 500}
    return {
        "allowed": allowed,
        "reason": reason,
        "policy_revision": POLICY_REVISION,
        "normalized_parameters": parameters,
    }
