import copy

import pytest

from casedesk.retrieval import KnowledgeBase, tokenize


@pytest.mark.parametrize(
    "query,expected",
    [
        ("退款 30 天", "refund.eligibility"),
        ("refund eligibility delivered", "refund.eligibility"),
        ("delivery delay coupon", "coupon.delivery"),
        ("配送延误优惠券", "coupon.delivery"),
        ("account takeover security", "security.escalation"),
        ("请求订单编号账户验证", "account.verification"),
    ],
)
def test_bilingual_bm25_finds_expected_policy(query, expected):
    kb = KnowledgeBase()
    result = kb.search(query)
    assert result == kb.search(query)
    assert expected in [chunk["id"] for chunk in result["chunks"][:3]]
    assert all(chunk["revision"] == kb.revision for chunk in result["chunks"])
    scores = [chunk["score"] for chunk in result["chunks"]]
    assert scores == sorted(scores, reverse=True)


def test_chinese_bigrams_and_english_case_normalization():
    assert tokenize("Refund 退款") == ["refund", "退", "款", "退款"]


def test_missing_terms_return_no_fabricated_evidence():
    assert KnowledgeBase().search("zqxjkv", 3)["chunks"] == []


@pytest.mark.parametrize(
    "query,limit",
    [("", 5), (" " * 4, 5), ("x" * 501, 5), ("refund", 0), ("refund", 6), ("refund", True)],
)
def test_search_bounds(query, limit):
    with pytest.raises(ValueError):
        KnowledgeBase().search(query, limit)


@pytest.mark.parametrize("change", ["unretrieved", "quote", "revision", "chunk", "modified_source"])
def test_citations_require_exact_current_retrieved_source(change):
    kb = KnowledgeBase()
    chunks = kb.search("refund")["chunks"]
    chosen = chunks[0]
    citation = {"chunk_id": chosen["id"], "revision": kb.revision, "quote": chosen["text"]}
    kb.verify([citation], chunks)
    if change == "unretrieved":
        chunks = []
    elif change == "quote":
        citation["quote"] = "The model fabricated a refund policy."
    elif change == "revision":
        citation["revision"] = "old-version"
    elif change == "chunk":
        citation["chunk_id"] = "nonexistent"
    else:
        chunks = copy.deepcopy(chunks)
        chunks[0]["text"] += " changed"
    with pytest.raises(ValueError):
        kb.verify([citation], chunks)
