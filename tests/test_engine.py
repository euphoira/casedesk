import asyncio
import copy

import pytest

from casedesk.config import Settings
from casedesk.engine import Engine
from casedesk.evaluation import evaluate
from casedesk.providers import Completion, ProviderError, ToolCall


class ScriptedProvider:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    async def complete(self, messages, tools):
        self.messages.append(copy.deepcopy(messages))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


def resolution(**extra):
    return {
        "classification": "other",
        "draft": "请补充订单信息，当前没有执行任何操作。",
        "confidence": "low",
        "citations": [],
        "action": "request_info",
        "limitations": [],
        **extra,
    }


def live_engine(setup, responses, **kwargs):
    settings, store, kb, _ = setup
    configured = Settings(
        mode="live", model="test-model", api_key="test-only", data_dir=settings.data_dir, **kwargs
    )
    provider = ScriptedProvider(responses)
    return Engine(configured, store, kb, provider), provider


async def test_live_retrieves_cites_and_proposes_without_executing(setup):
    _, store, kb, _ = setup
    chunk = kb.search("refund")["chunks"][0]
    cited = {"chunk_id": chunk["id"], "revision": kb.revision, "quote": chunk["text"]}
    engine, provider = live_engine(
        setup,
        [
            Completion([ToolCall("search-1", "search_knowledge", {"query": "refund"})]),
            Completion([ToolCall("draft-1", "propose_resolution", resolution(citations=[cited]))]),
        ],
    )
    result = await engine.analyze("CS-1001")
    assert result["status"] == "completed", result["error"]
    assert result["model_calls"] == 2
    assert result["citations"] == [cited]
    assert provider.messages[1][-1]["tool_call_id"] == "search-1"
    assert store.effects() == []


async def test_quoted_but_unretrieved_evidence_cannot_create_proposal(setup):
    _, store, kb, _ = setup
    chunk = kb.chunks["refund.eligibility"]
    engine, _ = live_engine(
        setup,
        [
            Completion(
                [
                    ToolCall(
                        "draft",
                        "propose_resolution",
                        resolution(
                            citations=[
                                {
                                    "chunk_id": chunk["id"],
                                    "revision": kb.revision,
                                    "quote": chunk["text"],
                                }
                            ]
                        ),
                    )
                ]
            )
        ],
    )
    result = await engine.analyze("CS-1001")
    assert result["status"] == "failed"
    assert result["proposal"] is None
    assert store.effects() == []


async def test_model_cannot_propose_arbitrary_amount_payload(setup):
    engine, _ = live_engine(
        setup,
        [Completion([ToolCall("draft", "propose_resolution", resolution(amount_cents=999999))])],
    )
    result = await engine.analyze("CS-1001")
    assert result["status"] == "failed" and result["proposal"] is None


async def test_missing_evidence_for_refund_is_policy_denied(setup):
    engine, _ = live_engine(
        setup,
        [
            Completion(
                [
                    ToolCall(
                        "draft",
                        "propose_resolution",
                        resolution(action="refund", confidence="high"),
                    )
                ]
            )
        ],
    )
    result = await engine.analyze("CS-1001")
    assert result["status"] == "completed"
    assert result["confidence"] == "low"
    assert not result["proposal"]["policy"]["allowed"]


async def test_model_budget_stops_infinite_tool_loop(setup):
    engine, provider = live_engine(
        setup, [Completion([ToolCall("read", "get_ticket", {})])] * 3, max_model_calls=3
    )
    result = await engine.analyze("CS-1001")
    assert result["status"] == "failed"
    assert len(provider.messages) == result["model_calls"] == 3


async def test_model_cannot_execute_unknown_tool(setup):
    engine, provider = live_engine(
        setup,
        [
            Completion([ToolCall("bad", "send_payment", {"amount": 999})]),
            Completion([ToolCall("draft", "propose_resolution", resolution())]),
        ],
    )
    result = await engine.analyze("CS-1001")
    assert result["status"] == "completed"
    assert "error" in provider.messages[1][-1]["content"]
    assert setup[1].effects() == []


async def test_mixed_submission_and_search_fails(setup):
    engine, _ = live_engine(
        setup,
        [
            Completion(
                [
                    ToolCall("search", "search_knowledge", {"query": "refund"}),
                    ToolCall("draft", "propose_resolution", resolution()),
                ]
            )
        ],
    )
    assert (await engine.analyze("CS-1001"))["status"] == "failed"


async def test_ticket_changed_while_model_runs_cannot_publish_approval(setup):
    _, store, kb, _ = setup

    class ChangeDuringCall:
        async def complete(self, messages, tools):
            store.update_ticket("CS-1001", 1, "escalated")
            return Completion([ToolCall("draft", "propose_resolution", resolution())])

    engine = Engine(
        Settings(mode="live", model="test", api_key="test"), store, kb, ChangeDuringCall()
    )
    result = await engine.analyze("CS-1001")
    assert result["status"] == "failed" and result["proposal"] is None


async def test_provider_failure_sanitized_and_cancelled_analysis_recorded(setup):
    engine, _ = live_engine(setup, [ProviderError("Connection timed out")])
    result = await engine.analyze("CS-1001")
    assert result["status"] == "failed" and result["error"] == "Connection timed out"
    engine, _ = live_engine(setup, [asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await engine.analyze("CS-1001")
    assert setup[1].latest_analysis("CS-1001")["status"] == "failed"


def test_restart_recovers_running_analysis_without_effect(setup):
    _, store, _, _ = setup
    store.begin_analysis("CS-1001", "live")
    store.recover()
    assert store.latest_analysis("CS-1001")["status"] == "failed"
    assert store.effects() == []


async def test_evaluation_is_offline_synthetic_and_reproducible(tmp_path):
    result = await evaluate(tmp_path / "evaluation.json")
    assert result["passed"] == result["total"] == 9
    assert result["mode"] == "demo"
    assert "NOT" in result["scope"]
