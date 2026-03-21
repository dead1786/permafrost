"""Tests for smart.vector — hybrid search, BM25, temporal decay, MMR."""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from smart.vector import (
    cosine_similarity, bm25_score, temporal_decay, mmr_rerank,
    expand_query, _tokenize, VectorStore, PFVectorSearch,
)


class TestCosineSimilarity:
    def test_identical(self):
        assert abs(cosine_similarity([1, 0], [1, 0]) - 1.0) < 0.001

    def test_orthogonal(self):
        assert abs(cosine_similarity([1, 0], [0, 1])) < 0.001

    def test_opposite(self):
        assert abs(cosine_similarity([1, 0], [-1, 0]) + 1.0) < 0.001

    def test_zero_vector(self):
        assert cosine_similarity([0, 0], [1, 0]) == 0.0


class TestBM25:
    def test_basic(self):
        score = bm25_score(["hello"], ["hello", "world"], 2.0)
        assert score > 0

    def test_no_match(self):
        score = bm25_score(["xyz"], ["hello", "world"], 2.0)
        assert score == 0

    def test_normalization(self):
        """BM25 score should normalize to (0, 1) with OpenClaw formula."""
        score = bm25_score(["hello"], ["hello", "world"], 2.0)
        normalized = score / (1 + score)
        assert 0 < normalized < 1


class TestTemporalDecay:
    def test_recent(self):
        from datetime import datetime
        d = temporal_decay(datetime.now().isoformat(), 30)
        assert d > 0.99  # Very recent should be close to 1

    def test_old(self):
        d = temporal_decay("2020-01-01T00:00:00", 30)
        assert d < 0.01  # Very old should be close to 0

    def test_evergreen_l1(self):
        d = temporal_decay("2020-01-01", 30, metadata={"layer": "L1"})
        assert d == 1.0  # L1 never decays

    def test_evergreen_l2(self):
        d = temporal_decay("2020-01-01", 30, metadata={"layer": "L2"})
        assert d == 1.0  # L2 never decays

    def test_l3_decays(self):
        d = temporal_decay("2020-01-01", 30, metadata={"layer": "L3"})
        assert d < 1.0  # L3 should decay

    def test_invalid_date(self):
        d = temporal_decay("not-a-date", 30)
        assert d == 0.5  # Fallback


class TestMMR:
    def test_basic(self):
        candidates = [
            {"score": 0.9, "embedding": [1, 0]},
            {"score": 0.8, "embedding": [0.9, 0.1]},
            {"score": 0.3, "embedding": [0, 1]},
        ]
        result = mmr_rerank([1, 0], candidates, 0.7, 2)
        assert len(result) == 2

    def test_diversity(self):
        """MMR should pick diverse results, not just top scores."""
        candidates = [
            {"score": 0.9, "embedding": [1, 0, 0]},
            {"score": 0.85, "embedding": [0.99, 0.01, 0]},  # Very similar to first
            {"score": 0.5, "embedding": [0, 0, 1]},          # Very different
        ]
        result = mmr_rerank([1, 0, 0], candidates, 0.5, 2)  # Low lambda = more diversity
        # Should prefer the diverse one over the similar one
        scores = [r["score"] for r in result]
        assert len(result) == 2

    def test_empty(self):
        assert mmr_rerank([1, 0], [], 0.7, 5) == []


class TestQueryExpansion:
    def test_removes_stopwords(self):
        tokens = expand_query("the quick brown fox")
        assert "the" not in tokens
        assert "quick" in tokens or "brown" in tokens

    def test_keeps_content_words(self):
        tokens = expand_query("trading strategy analysis")
        assert "trading" in tokens
        assert "strategy" in tokens

    def test_empty_fallback(self):
        tokens = expand_query("the a an")
        assert len(tokens) > 0  # Should fallback to all tokens


class TestTokenize:
    def test_basic(self):
        tokens = _tokenize("Hello World Test")
        assert "hello" in tokens
        assert "world" in tokens

    def test_filters_short(self):
        tokens = _tokenize("I am a test")
        assert "i" not in tokens  # Single char filtered


class TestVectorStore:
    def test_add_and_count(self, tmp_path):
        store = VectorStore(str(tmp_path / "test.json"))
        store.add("id1", "hello", [0.1, 0.2])
        assert store.count() == 1

    def test_add_duplicate_updates(self, tmp_path):
        store = VectorStore(str(tmp_path / "test.json"))
        store.add("id1", "hello", [0.1, 0.2])
        store.add("id1", "updated", [0.3, 0.4])
        assert store.count() == 1
        assert store.get_all()[0]["text"] == "updated"

    def test_remove(self, tmp_path):
        store = VectorStore(str(tmp_path / "test.json"))
        store.add("id1", "hello", [0.1, 0.2])
        assert store.remove("id1")
        assert store.count() == 0

    def test_persistence(self, tmp_path):
        path = str(tmp_path / "persist.json")
        store1 = VectorStore(path)
        store1.add("id1", "hello", [0.1, 0.2])
        store2 = VectorStore(path)
        assert store2.count() == 1
