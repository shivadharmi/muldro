"""Local fastembed embedding + reranker services (no external API, no model download).

The real ONNX model is bypassed by injecting a fake into the service's singleton
slot, so these run fast and offline.
"""

import numpy as np

from src.services.embedding_service import EmbeddingService
from src.services.reranker_service import RerankerService
from tests.conftest import make_mock_settings


class _FakeEmbedModel:
    def __init__(self, dim: int = 768):
        self._dim = dim

    def embed(self, texts):
        for _ in texts:
            yield np.arange(self._dim, dtype=np.float32) / self._dim


class _FakeCrossEncoder:
    def __init__(self, scores_by_text: dict[str, float]):
        self._scores = scores_by_text

    def rerank(self, query, texts):
        for t in texts:
            yield self._scores.get(t, 0.0)


def _embed_service() -> EmbeddingService:
    s = make_mock_settings()
    s.embedding_model = "BAAI/bge-base-en-v1.5"
    svc = EmbeddingService(s)
    svc._model = _FakeEmbedModel(768)  # bypass lazy ONNX load
    return svc


def _reranker(scores: dict[str, float], enabled: bool = True) -> RerankerService:
    s = make_mock_settings()
    s.reranker_model = "Xenova/ms-marco-MiniLM-L-12-v2"
    s.reranker_enabled = enabled
    svc = RerankerService(s)
    svc._model = _FakeCrossEncoder(scores)
    return svc


async def test_embed_text_returns_768_floats():
    svc = _embed_service()
    vec = await svc.embed_text("hello world")
    assert vec is not None
    assert len(vec) == 768
    assert all(isinstance(x, float) for x in vec)


async def test_embed_text_empty_returns_none():
    svc = _embed_service()
    assert await svc.embed_text("   ") is None


async def test_embed_texts_batch():
    svc = _embed_service()
    out = await svc.embed_texts(["a", "b", "c"])
    assert out is not None and len(out) == 3 and len(out[0]) == 768


async def test_rerank_orders_by_score_and_annotates():
    docs = [
        {"id": "1", "text": "bananas are yellow", "score": 0.9},
        {"id": "2", "text": "paris is the capital of france", "score": 0.1},
    ]
    svc = _reranker({"bananas are yellow": -11.0, "paris is the capital of france": 9.0})
    out = await svc.rerank("capital of France", docs, top_k=5)
    assert [d["id"] for d in out] == ["2", "1"]  # relevant doc first, despite lower orig score
    assert out[0]["rerank_score"] == 9.0


async def test_rerank_respects_top_k():
    docs = [{"id": str(i), "text": f"doc {i}", "score": 0} for i in range(5)]
    svc = _reranker({f"doc {i}": float(i) for i in range(5)})
    out = await svc.rerank("q", docs, top_k=2)
    assert len(out) == 2
    assert [d["id"] for d in out] == ["4", "3"]


async def test_rerank_disabled_falls_back_to_score_order():
    docs = [{"id": "a", "text": "x", "score": 0.2}, {"id": "b", "text": "y", "score": 0.8}]
    svc = _reranker({}, enabled=False)
    out = await svc.rerank("q", docs)
    assert [d["id"] for d in out] == ["b", "a"]  # original score order, no rerank_score
    assert "rerank_score" not in out[0]
