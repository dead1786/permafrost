"""
Permafrost Vector Search — Embedding-based semantic memory retrieval.

Provides hybrid search combining:
  1. Vector similarity (cosine) for semantic matching
  2. BM25 keyword scoring for exact term matching
  3. Temporal decay (recent memories score higher)
  4. MMR reranking for result diversity

Embedding providers (provider-agnostic):
  - Local: sentence-transformers (all-MiniLM-L6-v2)
  - API: OpenAI text-embedding-3-small, Gemini embedding-001

Storage: JSON + numpy arrays (lightweight, no external DB dependency)
Upgrade path: swap storage backend to LanceDB/FAISS for scale.
"""

import json
import logging
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("permafrost.vector")

# ── Embedding Providers ───────────────────────────────────────────


class EmbeddingProvider:
    """Base class for embedding providers."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def dimension(self) -> int:
        raise NotImplementedError


class LocalEmbedding(EmbeddingProvider):
    """Local embedding using sentence-transformers."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model = None
        self._model_name = model_name

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
                log.info(f"Loaded local embedding: {self._model_name}")
            except ImportError:
                raise RuntimeError(
                    "sentence-transformers not installed. "
                    "Run: pip install sentence-transformers"
                )

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._load()
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()

    @property
    def dimension(self) -> int:
        return 384  # all-MiniLM-L6-v2


class OpenAIEmbedding(EmbeddingProvider):
    """OpenAI API embedding."""

    def __init__(self, api_key: str, model: str = "text-embedding-3-small"):
        self.api_key = api_key
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        import openai
        client = openai.OpenAI(api_key=self.api_key)
        resp = client.embeddings.create(input=texts, model=self.model)
        return [item.embedding for item in resp.data]

    @property
    def dimension(self) -> int:
        return 1536  # text-embedding-3-small


class GeminiEmbedding(EmbeddingProvider):
    """Google Gemini API embedding."""

    def __init__(self, api_key: str, model: str = "models/embedding-001"):
        self.api_key = api_key
        self.model = model

    def embed(self, texts: list[str]) -> list[list[float]]:
        import google.generativeai as genai
        genai.configure(api_key=self.api_key)
        results = []
        for text in texts:
            resp = genai.embed_content(
                model=self.model,
                content=text,
                task_type="retrieval_document",
            )
            results.append(resp["embedding"])
        return results

    @property
    def dimension(self) -> int:
        return 768  # embedding-001


def create_embedder(provider: str = "local", api_key: str = "", **kwargs) -> EmbeddingProvider:
    """Factory: create an embedding provider."""
    if provider == "local":
        return LocalEmbedding(kwargs.get("model", "all-MiniLM-L6-v2"))
    elif provider == "openai":
        return OpenAIEmbedding(api_key, kwargs.get("model", "text-embedding-3-small"))
    elif provider == "gemini":
        return GeminiEmbedding(api_key, kwargs.get("model", "models/embedding-001"))
    else:
        raise ValueError(f"Unknown embedding provider: {provider}")


# ── Vector Store ──────────────────────────────────────────────────


class VectorStore:
    """JSON + list-based vector store. Lightweight, no external deps.

    Each entry: {id, text, embedding, metadata, created_at}
    Embeddings stored as JSON arrays (small scale: <10k entries).
    """

    def __init__(self, store_path: str):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict] = []
        self._load()

    def _load(self):
        if self.store_path.exists():
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    self._entries = json.load(f)
                log.debug(f"Loaded {len(self._entries)} vectors from {self.store_path}")
            except (json.JSONDecodeError, OSError):
                self._entries = []

    def _save(self):
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, ensure_ascii=False)

    def add(self, entry_id: str, text: str, embedding: list[float],
            metadata: dict = None, created_at: str = None):
        """Add or update a vector entry."""
        # Update if exists
        for e in self._entries:
            if e["id"] == entry_id:
                e["text"] = text
                e["embedding"] = embedding
                e["metadata"] = metadata or {}
                e["updated_at"] = datetime.now().isoformat()
                self._save()
                return

        self._entries.append({
            "id": entry_id,
            "text": text,
            "embedding": embedding,
            "metadata": metadata or {},
            "created_at": created_at or datetime.now().isoformat(),
        })
        self._save()

    def remove(self, entry_id: str) -> bool:
        before = len(self._entries)
        self._entries = [e for e in self._entries if e["id"] != entry_id]
        if len(self._entries) < before:
            self._save()
            return True
        return False

    def get_all(self) -> list[dict]:
        return self._entries

    def count(self) -> int:
        return len(self._entries)

    def clear(self):
        self._entries = []
        self._save()


# ── BM25 Keyword Scoring ─────────────────────────────────────────


def _tokenize(text: str) -> list[str]:
    """Simple tokenizer: lowercase, split on non-alphanum, filter short tokens."""
    tokens = re.findall(r'[\w\u4e00-\u9fff\u3400-\u4dbf]+', text.lower())
    return [t for t in tokens if len(t) > 1]


def bm25_score(query_tokens: list[str], doc_tokens: list[str],
               avg_dl: float, k1: float = 1.5, b: float = 0.75) -> float:
    """BM25 scoring for a single document."""
    dl = len(doc_tokens)
    score = 0.0
    doc_tf = {}
    for t in doc_tokens:
        doc_tf[t] = doc_tf.get(t, 0) + 1
    for qt in query_tokens:
        tf = doc_tf.get(qt, 0)
        if tf > 0:
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * (dl / max(avg_dl, 1)))
            score += numerator / denominator
    return score


# ── Cosine Similarity ─────────────────────────────────────────────


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Temporal Decay ────────────────────────────────────────────────


def temporal_decay(created_at: str, half_life_days: float = 30.0) -> float:
    """Exponential decay based on age. Returns weight in (0, 1]."""
    try:
        created = datetime.fromisoformat(created_at)
        age_days = (datetime.now() - created).total_seconds() / 86400
        return math.exp(-0.693 * age_days / half_life_days)  # ln(2) = 0.693
    except (ValueError, TypeError):
        return 0.5


# ── MMR Reranking ─────────────────────────────────────────────────


def mmr_rerank(query_embedding: list[float], candidates: list[dict],
               lambda_param: float = 0.7, top_k: int = 10) -> list[dict]:
    """Maximal Marginal Relevance reranking for result diversity.

    lambda_param: balance between relevance (1.0) and diversity (0.0).
    """
    if not candidates or top_k <= 0:
        return []

    selected = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_score = -float("inf")
        best_idx = 0

        for i, cand in enumerate(remaining):
            relevance = cosine_similarity(query_embedding, cand["embedding"])

            # Max similarity to already-selected items
            max_sim = 0.0
            for sel in selected:
                sim = cosine_similarity(cand["embedding"], sel["embedding"])
                if sim > max_sim:
                    max_sim = sim

            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_sim

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        selected.append(remaining.pop(best_idx))

    return selected


# ── Hybrid Search Engine ──────────────────────────────────────────


class PFVectorSearch:
    """Hybrid search combining vector similarity, BM25, and temporal decay.

    Usage:
        vs = PFVectorSearch(data_dir="/path/to/.permafrost")
        vs.index_memory(entry_id, text, metadata)  # index a memory
        results = vs.search("query text", top_k=5)  # hybrid search
    """

    def __init__(self, data_dir: str, config: dict = None):
        self.data_dir = Path(data_dir)
        config = config or {}

        # Vector store
        self.store = VectorStore(str(self.data_dir / "memory" / "vectors.json"))

        # Embedding provider (lazy init)
        self._embedder: Optional[EmbeddingProvider] = None
        self._embed_provider = config.get("embedding_provider", "local")
        self._embed_api_key = config.get("api_key", config.get("embedding_api_key", ""))
        self._embed_model = config.get("embedding_model", "")

        # Search weights
        self.vector_weight = config.get("vector_weight", 0.6)
        self.bm25_weight = config.get("bm25_weight", 0.3)
        self.temporal_weight = config.get("temporal_weight", 0.1)
        self.decay_half_life = config.get("decay_half_life_days", 30.0)

        # MMR
        self.mmr_lambda = config.get("mmr_lambda", 0.7)

    @property
    def embedder(self) -> EmbeddingProvider:
        if self._embedder is None:
            self._embedder = create_embedder(
                provider=self._embed_provider,
                api_key=self._embed_api_key,
                model=self._embed_model,
            )
        return self._embedder

    def index_memory(self, entry_id: str, text: str, metadata: dict = None,
                     created_at: str = None):
        """Index a single memory entry for vector search."""
        try:
            embedding = self.embedder.embed_one(text)
            self.store.add(entry_id, text, embedding, metadata, created_at)
            log.debug(f"Indexed: {entry_id}")
        except Exception as e:
            log.error(f"Indexing failed for {entry_id}: {e}")

    def index_batch(self, entries: list[dict]):
        """Batch index multiple entries. Each: {id, text, metadata?, created_at?}"""
        if not entries:
            return

        texts = [e["text"] for e in entries]
        try:
            embeddings = self.embedder.embed(texts)
            for entry, emb in zip(entries, embeddings):
                self.store.add(
                    entry["id"], entry["text"], emb,
                    entry.get("metadata"), entry.get("created_at"),
                )
            log.info(f"Batch indexed {len(entries)} entries")
        except Exception as e:
            log.error(f"Batch indexing failed: {e}")

    def remove(self, entry_id: str) -> bool:
        return self.store.remove(entry_id)

    def search(self, query: str, top_k: int = 5, use_mmr: bool = True) -> list[dict]:
        """Hybrid search: vector + BM25 + temporal decay.

        Returns list of {id, text, metadata, score, vector_score, bm25_score, temporal_score}.
        """
        entries = self.store.get_all()
        if not entries:
            return []

        # Get query embedding
        try:
            query_embedding = self.embedder.embed_one(query)
        except Exception as e:
            log.error(f"Query embedding failed: {e}")
            # Fallback to BM25-only
            return self._bm25_only_search(query, entries, top_k)

        # Tokenize for BM25
        query_tokens = _tokenize(query)
        all_doc_tokens = [_tokenize(e["text"]) for e in entries]
        avg_dl = sum(len(dt) for dt in all_doc_tokens) / max(len(all_doc_tokens), 1)

        # Score each entry
        scored = []
        for i, entry in enumerate(entries):
            vec_score = cosine_similarity(query_embedding, entry["embedding"])
            bm_score = bm25_score(query_tokens, all_doc_tokens[i], avg_dl)
            temp_score = temporal_decay(
                entry.get("created_at", ""),
                self.decay_half_life,
            )

            # Normalize BM25 (rough: cap at 10)
            bm_norm = min(bm_score / 10.0, 1.0)

            # Weighted combination
            combined = (
                self.vector_weight * vec_score +
                self.bm25_weight * bm_norm +
                self.temporal_weight * temp_score
            )

            scored.append({
                "id": entry["id"],
                "text": entry["text"],
                "metadata": entry.get("metadata", {}),
                "embedding": entry["embedding"],
                "created_at": entry.get("created_at", ""),
                "score": combined,
                "vector_score": vec_score,
                "bm25_score": bm_score,
                "temporal_score": temp_score,
            })

        # Sort by combined score
        scored.sort(key=lambda x: x["score"], reverse=True)

        # MMR reranking for diversity
        if use_mmr and len(scored) > top_k:
            scored = mmr_rerank(query_embedding, scored, self.mmr_lambda, top_k)
        else:
            scored = scored[:top_k]

        # Clean up (remove embedding from results)
        for r in scored:
            r.pop("embedding", None)

        return scored

    def _bm25_only_search(self, query: str, entries: list[dict], top_k: int) -> list[dict]:
        """Fallback: BM25-only search when embedding fails."""
        query_tokens = _tokenize(query)
        all_doc_tokens = [_tokenize(e["text"]) for e in entries]
        avg_dl = sum(len(dt) for dt in all_doc_tokens) / max(len(all_doc_tokens), 1)

        scored = []
        for i, entry in enumerate(entries):
            bm = bm25_score(query_tokens, all_doc_tokens[i], avg_dl)
            if bm > 0:
                scored.append({
                    "id": entry["id"],
                    "text": entry["text"],
                    "metadata": entry.get("metadata", {}),
                    "score": bm,
                    "vector_score": 0,
                    "bm25_score": bm,
                    "temporal_score": 0,
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def rebuild_index(self, memories: list[dict]):
        """Full reindex: clear store and re-embed all memories.

        Each memory: {id, text, metadata?, created_at?}
        """
        self.store.clear()
        if memories:
            self.index_batch(memories)
            log.info(f"Full reindex complete: {len(memories)} entries")

    def get_stats(self) -> dict:
        return {
            "total_vectors": self.store.count(),
            "embedding_provider": self._embed_provider,
            "store_path": str(self.store.store_path),
        }
