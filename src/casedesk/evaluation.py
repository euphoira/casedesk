"""Offline synthetic regression, explicitly not live-model quality measurement."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from casedesk.config import Settings
from casedesk.engine import Engine
from casedesk.retrieval import KnowledgeBase
from casedesk.store import Conflict, Store


async def evaluate(output: Path | None = None) -> dict:
    rows = []
    with TemporaryDirectory(prefix="casedesk-eval-") as directory:
        settings = Settings(data_dir=Path(directory))
        store, kb = Store(settings.data_dir / "eval.sqlite3"), KnowledgeBase()
        engine = Engine(settings, store, kb)
        expected = [
            ("CS-1001", "refund", True),
            ("CS-1002", "refund", False),
            ("CS-1003", "request_info", True),
            ("CS-1004", "escalate", True),
            ("CS-1005", "coupon", True),
        ]
        for ticket_id, action, allowed in expected:
            result = await engine.analyze(ticket_id)
            proposal = result.get("proposal") or {}
            passed = (
                result["status"] == "completed"
                and proposal.get("action") == action
                and proposal.get("policy", {}).get("allowed") is allowed
            )
            rows.append({"case": ticket_id, "passed": passed, "kind": "fixture_draft_and_policy"})
        for query, expected_chunk in [
            ("退款 30 天 refund", "refund.eligibility"),
            ("delivery delay coupon", "coupon.delivery"),
        ]:
            found = kb.search(query)["chunks"]
            rows.append(
                {
                    "case": query,
                    "passed": any(c["id"] == expected_chunk for c in found[:3]),
                    "kind": "retrieval",
                }
            )
        rejected = False
        try:
            kb.verify(
                [
                    {
                        "chunk_id": "refund.eligibility",
                        "revision": kb.revision,
                        "quote": "invented refund policy",
                    }
                ],
                [],
            )
        except ValueError:
            rejected = True
        rows.append({"case": "fabricated-citation", "passed": rejected, "kind": "provenance"})
        proposal = store.latest_analysis("CS-1001")["proposal"]
        store.update_ticket("CS-1001", 1, "escalated")
        rejected = False
        try:
            store.approve(proposal["id"], 1, "eval-stale-key", kb)
        except Conflict:
            rejected = True
        rows.append(
            {
                "case": "stale-approval",
                "passed": rejected and not store.effects(),
                "kind": "approval",
            }
        )
    result = {
        "mode": "demo",
        "scope": "9 synthetic regression cases; NOT real-model accuracy or payment processing",
        "passed": sum(bool(row["passed"]) for row in rows),
        "total": len(rows),
        "cases": rows,
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result
