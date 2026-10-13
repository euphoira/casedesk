"""Atomic approvals, optimistic ticket revisions and exactly-once local effects."""

import copy
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from casedesk import policy
from casedesk.fixtures import TICKETS


def now() -> str:
    return datetime.now(UTC).isoformat()


class Conflict(ValueError):
    pass


class Denied(ValueError):
    pass


def relevant_evidence(action: str, citations: list[dict]) -> bool:
    expected = {"refund": "refund.eligibility", "coupon": "coupon.delivery"}.get(action)
    return any(item["chunk_id"] == expected for item in citations) if expected else bool(citations)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, latest_analysis TEXT
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS effects (
                    id TEXT PRIMARY KEY, proposal_id TEXT UNIQUE NOT NULL,
                    ticket_id TEXT NOT NULL, order_id TEXT NOT NULL,
                    action TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS effect_once_per_order
                    ON effects(order_id, action) WHERE action IN ('refund', 'coupon');
                CREATE TABLE IF NOT EXISTS idempotency (
                    key TEXT PRIMARY KEY, proposal_id TEXT NOT NULL,
                    ticket_revision INTEGER NOT NULL, effect_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL,
                    event TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
            for fixture in TICKETS:
                ticket = {
                    **copy.deepcopy(fixture),
                    "revision": 1,
                    "status": "open",
                    "created_at": now(),
                }
                conn.execute(
                    "INSERT OR IGNORE INTO tickets VALUES (?, ?, NULL)",
                    (ticket["id"], json.dumps(ticket)),
                )

    @contextmanager
    def connection(self, *, immediate=False):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                if immediate:
                    conn.execute("BEGIN IMMEDIATE")
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _document(conn, table: str, identifier: str) -> dict:
        # Table names are internal constants, never caller input.
        row = conn.execute(f"SELECT document FROM {table} WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Not found")
        return json.loads(row["document"])

    @staticmethod
    def _save(conn, table: str, document: dict):
        conn.execute(
            f"UPDATE {table} SET document=? WHERE id=?", (json.dumps(document), document["id"])
        )

    @staticmethod
    def _audit(conn, ticket_id: str, event: str, data: dict):
        conn.execute(
            "INSERT INTO audit(ticket_id,event,data,created_at) VALUES (?,?,?,?)",
            (ticket_id, event, json.dumps(data), now()),
        )

    @staticmethod
    def _effective_ticket(conn, ticket: dict) -> dict:
        # Tickets share server-owned orders; a sibling ticket's committed refund is current fact.
        if conn.execute(
            "SELECT 1 FROM effects WHERE order_id=? AND action='refund'", (ticket["order_id"],)
        ).fetchone():
            ticket["order"]["refunded"] = True
        return ticket

    def tickets(self) -> list[dict]:
        with self.connection() as conn:
            return [
                self._effective_ticket(conn, json.loads(row[0]))
                for row in conn.execute("SELECT document FROM tickets ORDER BY id")
            ]

    def contexts(self) -> list[dict]:
        with self.connection() as conn:
            refunded = {
                row[0] for row in conn.execute("SELECT order_id FROM effects WHERE action='refund'")
            }
        result = []
        for fixture in TICKETS:
            order = copy.deepcopy(fixture["order"])
            order["refunded"] = order["refunded"] or fixture["order_id"] in refunded
            result.append(
                {
                    "id": fixture["order_id"],
                    "label": fixture["order_id"] + " · " + fixture["account_id"],
                    "account_id": fixture["account_id"],
                    "order_id": fixture["order_id"],
                    "account": copy.deepcopy(fixture["account"]),
                    "order": order,
                }
            )
        return result

    def create_ticket(self, subject: str, body: str, category: str, context_id: str) -> dict:
        context = next((item for item in TICKETS if item["order_id"] == context_id), None)
        if context is None:
            raise ValueError("Unknown synthetic account/order context")
        with self.connection(immediate=True) as conn:
            if conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] >= 200:
                raise Conflict("本地演示最多保存 200 个工单，请使用新的数据目录。")
            ticket = {
                **copy.deepcopy(context),
                "id": "CS-" + uuid4().hex[:10].upper(),
                "subject": subject,
                "body": body,
                "category": category,
                "revision": 1,
                "status": "open",
                "created_at": now(),
            }
            if conn.execute(
                "SELECT 1 FROM effects WHERE order_id=? AND action='refund'", (context_id,)
            ).fetchone():
                ticket["order"]["refunded"] = True
            conn.execute(
                "INSERT INTO tickets VALUES (?,?,NULL)", (ticket["id"], json.dumps(ticket))
            )
            self._audit(
                conn, ticket["id"], "ticket_created", {"context_id": context_id, "synthetic": True}
            )
            return ticket

    def ticket(self, ticket_id: str) -> dict:
        with self.connection() as conn:
            return self._effective_ticket(conn, self._document(conn, "tickets", ticket_id))

    def audit(self, ticket_id: str) -> list[dict]:
        self.ticket(ticket_id)
        with self.connection() as conn:
            return [
                {**dict(row), "data": json.loads(row["data"])}
                for row in conn.execute(
                    "SELECT * FROM audit WHERE ticket_id=? ORDER BY id", (ticket_id,)
                )
            ]

    def latest_analysis(self, ticket_id: str) -> dict:
        with self.connection() as conn:
            self._document(conn, "tickets", ticket_id)
            row = conn.execute(
                "SELECT latest_analysis FROM tickets WHERE id=?", (ticket_id,)
            ).fetchone()
            if not row[0]:
                raise KeyError("No analysis")
            analysis = self._document(conn, "analyses", row[0])
            if analysis.get("proposal"):
                analysis["proposal"] = self._document(conn, "proposals", analysis["proposal"]["id"])
            return analysis

    def begin_analysis(self, ticket_id: str, mode: str) -> tuple[dict, dict]:
        with self.connection(immediate=True) as conn:
            ticket = self._effective_ticket(conn, self._document(conn, "tickets", ticket_id))
            latest = conn.execute(
                "SELECT latest_analysis FROM tickets WHERE id=?", (ticket_id,)
            ).fetchone()[0]
            if latest and self._document(conn, "analyses", latest)["status"] == "running":
                raise Conflict("该工单正在分析，请等待当前任务完成。")
            analysis = {
                "id": uuid4().hex,
                "ticket_id": ticket_id,
                "ticket_revision": ticket["revision"],
                "status": "running",
                "mode": mode,
                "classification": "other",
                "draft": "",
                "confidence": "low",
                "citations": [],
                "retrieved": [],
                "proposal": None,
                "limitations": [],
                "error": None,
                "model_calls": 0,
                "created_at": now(),
            }
            conn.execute(
                "INSERT INTO analyses VALUES (?,?,?)",
                (analysis["id"], ticket_id, json.dumps(analysis)),
            )
            conn.execute(
                "UPDATE tickets SET latest_analysis=? WHERE id=?", (analysis["id"], ticket_id)
            )
            self._audit(
                conn, ticket_id, "analysis_started", {"analysis_id": analysis["id"], "mode": mode}
            )
            return ticket, analysis

    def save_progress(self, analysis: dict, event: str, data: dict):
        with self.connection(immediate=True) as conn:
            self._save(conn, "analyses", analysis)
            self._audit(conn, analysis["ticket_id"], event, data)

    @staticmethod
    def _coupon_exists(conn, order_id: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM effects WHERE order_id=? AND action='coupon'", (order_id,)
            ).fetchone()
            is not None
        )

    def finish_analysis(self, analysis: dict, resolution: dict, kb) -> dict:
        kb.verify(resolution["citations"], analysis["retrieved"])
        with self.connection(immediate=True) as conn:
            ticket = self._effective_ticket(
                conn, self._document(conn, "tickets", analysis["ticket_id"])
            )
            if ticket["revision"] != analysis["ticket_revision"]:
                raise Conflict("分析期间工单版本已变化，请重新分析。")
            verdict = policy.decide(
                ticket,
                resolution["action"],
                has_evidence=relevant_evidence(resolution["action"], resolution["citations"]),
                coupon_exists=self._coupon_exists(conn, ticket["order_id"]),
            )
            proposal = {
                "id": uuid4().hex,
                "ticket_id": ticket["id"],
                "ticket_revision": ticket["revision"],
                "analysis_id": analysis["id"],
                "action": resolution["action"],
                "parameters": verdict["normalized_parameters"],
                "status": "pending",
                "policy": verdict,
                "corpus_revision": kb.revision,
                "created_at": now(),
            }
            analysis.update({key: value for key, value in resolution.items() if key != "action"})
            analysis.update(status="completed", proposal=proposal)
            if not analysis["citations"]:
                analysis["confidence"] = "low"
                analysis["limitations"] = analysis["limitations"][:5] + [
                    "未取得可核验知识引用，草稿仅供人工核查。"
                ]
            conn.execute(
                "INSERT INTO proposals VALUES (?,?,?)",
                (proposal["id"], ticket["id"], json.dumps(proposal)),
            )
            self._save(conn, "analyses", analysis)
            self._audit(
                conn, ticket["id"], "policy_evaluated", {"proposal_id": proposal["id"], **verdict}
            )
            self._audit(
                conn,
                ticket["id"],
                "draft_ready",
                {"analysis_id": analysis["id"], "simulation": True},
            )
        return analysis

    def fail_analysis(self, analysis: dict, error: str):
        analysis.update(status="failed", error=error, proposal=None)
        self.save_progress(analysis, "analysis_failed", {"error": error})
        return analysis

    def recover(self):
        with self.connection(immediate=True) as conn:
            for row in conn.execute("SELECT document FROM analyses").fetchall():
                analysis = json.loads(row[0])
                if analysis["status"] == "running":
                    analysis.update(
                        status="failed", error="上次进程中断，请重新分析。", proposal=None
                    )
                    self._save(conn, "analyses", analysis)
                    self._audit(
                        conn,
                        analysis["ticket_id"],
                        "analysis_interrupted",
                        {"analysis_id": analysis["id"]},
                    )

    def update_ticket(self, ticket_id: str, expected_revision: int, status: str):
        with self.connection(immediate=True) as conn:
            ticket = self._document(conn, "tickets", ticket_id)
            if ticket["revision"] != expected_revision:
                raise Conflict("工单版本已变化，请刷新。")
            ticket.update(status=status, revision=ticket["revision"] + 1)
            self._save(conn, "tickets", ticket)
            self._audit(
                conn,
                ticket_id,
                "ticket_updated",
                {"status": status, "revision": ticket["revision"]},
            )
            return ticket

    def approve(self, proposal_id: str, ticket_revision: int, idempotency_key: str, kb) -> dict:
        failure, result = None, None
        with self.connection(immediate=True) as conn:
            proposal = self._document(conn, "proposals", proposal_id)
            ticket = self._effective_ticket(
                conn, self._document(conn, "tickets", proposal["ticket_id"])
            )
            try:
                previous = conn.execute(
                    "SELECT * FROM idempotency WHERE key=?", (idempotency_key,)
                ).fetchone()
                if previous:
                    if (
                        previous["proposal_id"] != proposal_id
                        or previous["ticket_revision"] != ticket_revision
                    ):
                        raise Conflict("幂等键已用于其他操作或版本。")
                    effect = self._document(conn, "effects", previous["effect_id"])
                    result = {"proposal": proposal, "effect": effect, "replayed": True}
                    self._audit(
                        conn, ticket["id"], "approval_replayed", {"proposal_id": proposal_id}
                    )
                else:
                    self._validate_pending(conn, proposal, ticket, ticket_revision)
                    if (
                        proposal["policy"]["policy_revision"] != policy.POLICY_REVISION
                        or proposal["corpus_revision"] != kb.revision
                    ):
                        raise Conflict("政策或知识库版本已变化，请重新分析。")
                    analysis = self._document(conn, "analyses", proposal["analysis_id"])
                    try:
                        kb.verify(analysis["citations"], analysis["retrieved"])
                    except ValueError:
                        raise Conflict("知识引用已过期，请重新分析。") from None
                    if (
                        proposal["action"] in {"refund", "coupon"}
                        and conn.execute(
                            "SELECT 1 FROM effects WHERE order_id=? AND action='refund'",
                            (ticket["order_id"],),
                        ).fetchone()
                    ):
                        raise Denied("该订单已通过其他工单完成模拟退款。")
                    decision = policy.decide(
                        ticket,
                        proposal["action"],
                        has_evidence=relevant_evidence(proposal["action"], analysis["citations"]),
                        coupon_exists=self._coupon_exists(conn, ticket["order_id"]),
                    )
                    self._audit(
                        conn,
                        ticket["id"],
                        "approval_policy_checked",
                        {"proposal_id": proposal_id, **decision},
                    )
                    if not decision["allowed"]:
                        raise Denied(decision["reason"])
                    effect = {
                        "id": uuid4().hex,
                        "proposal_id": proposal_id,
                        "ticket_id": ticket["id"],
                        "action": proposal["action"],
                        "parameters": decision["normalized_parameters"],
                        "simulation": True,
                        "created_at": now(),
                    }
                    conn.execute(
                        "INSERT INTO effects VALUES (?,?,?,?,?,?)",
                        (
                            effect["id"],
                            proposal_id,
                            ticket["id"],
                            ticket["order_id"],
                            proposal["action"],
                            json.dumps(effect),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO idempotency VALUES (?,?,?,?)",
                        (
                            idempotency_key,
                            proposal_id,
                            ticket_revision,
                            effect["id"],
                        ),
                    )
                    proposal.update(
                        status="approved",
                        parameters=decision["normalized_parameters"],
                        policy=decision,
                    )
                    ticket["revision"] += 1
                    if proposal["action"] == "refund":
                        ticket["order"]["refunded"] = True
                        ticket["status"] = "closed"
                    elif proposal["action"] == "escalate":
                        ticket["status"] = "escalated"
                    self._save(conn, "proposals", proposal)
                    self._save(conn, "tickets", ticket)
                    self._audit(
                        conn,
                        ticket["id"],
                        "simulation_applied",
                        {"effect": effect, "revision": ticket["revision"]},
                    )
                    result = {"proposal": proposal, "effect": effect, "replayed": False}
            except (Conflict, Denied) as exc:
                failure = exc
                self._audit(
                    conn,
                    ticket["id"],
                    "approval_denied",
                    {"proposal_id": proposal_id, "reason": str(exc)},
                )
        if failure:
            raise failure
        return result

    @staticmethod
    def _validate_pending(conn, proposal, ticket, expected_revision):
        latest = conn.execute(
            "SELECT latest_analysis FROM tickets WHERE id=?", (ticket["id"],)
        ).fetchone()[0]
        if proposal["status"] != "pending":
            raise Conflict("提案已经处理，不能重复执行。")
        if (
            ticket["revision"] != expected_revision
            or proposal["ticket_revision"] != expected_revision
        ):
            raise Conflict("工单版本已变化，旧审批失效，请重新分析。")
        if latest != proposal["analysis_id"]:
            raise Conflict("存在更新的分析，旧提案已失效。")

    def reject(self, proposal_id: str, ticket_revision: int):
        with self.connection(immediate=True) as conn:
            proposal = self._document(conn, "proposals", proposal_id)
            ticket = self._document(conn, "tickets", proposal["ticket_id"])
            self._validate_pending(conn, proposal, ticket, ticket_revision)
            proposal["status"] = "rejected"
            self._save(conn, "proposals", proposal)
            self._audit(conn, ticket["id"], "proposal_rejected", {"proposal_id": proposal_id})
            return proposal

    def effects(self) -> list[dict]:
        with self.connection() as conn:
            return [
                json.loads(row[0])
                for row in conn.execute("SELECT document FROM effects ORDER BY rowid")
            ]
