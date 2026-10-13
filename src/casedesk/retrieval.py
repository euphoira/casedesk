"""Deterministic BM25 over an immutable, versioned packaged knowledge corpus."""

import copy
import json
import math
import re
from collections import Counter
from pathlib import Path

CORPUS_FILE = Path(__file__).parent / "knowledge" / "corpus.json"


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.extend(segment)
        tokens.extend(segment[i : i + 2] for i in range(len(segment) - 1))
    return tokens


class KnowledgeBase:
    def __init__(self, document: dict | None = None):
        data = document if document is not None else json.loads(CORPUS_FILE.read_text("utf-8"))
        self.revision = data["revision"]
        self.chunks = {
            item["id"]: {**copy.deepcopy(item), "revision": self.revision}
            for item in data["chunks"]
        }
        if len(self.chunks) != len(data["chunks"]) or not self.chunks:
            raise ValueError("Knowledge chunk IDs must be unique and nonempty")
        self.terms = {key: Counter(tokenize(item["text"])) for key, item in self.chunks.items()}
        self.lengths = {key: sum(terms.values()) for key, terms in self.terms.items()}
        self.average_length = sum(self.lengths.values()) / len(self.chunks)
        self.frequency = Counter(term for terms in self.terms.values() for term in terms)

    def search(self, query: str, limit: int = 5) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise ValueError("Search query must contain 1 to 500 characters")
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("Search limit must be 1 to 5")
        scores = []
        for key, terms in self.terms.items():
            score = 0.0
            for term in sorted(set(tokenize(query))):
                frequency = terms.get(term, 0)
                if not frequency:
                    continue
                df = self.frequency[term]
                inverse = math.log(1 + (len(self.chunks) - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.5 * (
                    1 - 0.75 + 0.75 * self.lengths[key] / self.average_length
                )
                score += inverse * frequency * 2.5 / denominator
            if score > 0:
                scores.append((score, key))
        scores.sort(key=lambda pair: (-pair[0], pair[1]))
        return {
            "revision": self.revision,
            "chunks": [
                {**copy.deepcopy(self.chunks[key]), "score": round(score, 6)}
                for score, key in scores[:limit]
            ],
        }

    def verify(self, citations: list[dict], retrieved: list[dict]) -> None:
        inspected = {item["id"]: item for item in retrieved}
        for citation in citations:
            key = citation["chunk_id"]
            original = self.chunks.get(key)
            seen = inspected.get(key)
            if (
                original is None
                or seen is None
                or citation["revision"] != self.revision
                or seen["revision"] != self.revision
                or seen["text"] != original["text"]
                or not citation["quote"]
                or citation["quote"] not in original["text"]
            ):
                raise ValueError("Citation is stale, fabricated, or was not retrieved")
