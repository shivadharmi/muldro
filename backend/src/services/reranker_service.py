"""Reranker Service — neural re-ranking of search results with a local ONNX model.

Runs entirely on-host via fastembed (onnxruntime, no torch, no external API).
Default model: Xenova/ms-marco-MiniLM-L-12-v2 (Apache-2.0, ~0.12 GB). A cross-encoder
scores each (query, document) pair by relevance — much better than raw cosine/BM25.
The model is loaded lazily on first use (pre-download at deploy so the first request
does not stall).
"""

import asyncio
import logging
import threading

from fastembed.rerank.cross_encoder import TextCrossEncoder

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class RerankerService:
    """Re-rank search results using a local fastembed cross-encoder."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._model_name = settings.reranker_model
        self._enabled = settings.reranker_enabled
        self._model: TextCrossEncoder | None = None
        self._lock = threading.Lock()

    def _get_model(self) -> TextCrossEncoder:
        """Lazily construct the model once and reuse it (thread-safe singleton)."""
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = TextCrossEncoder(model_name=self._model_name)
        return self._model

    async def rerank(
        self,
        query: str,
        documents: list[dict],
        top_k: int = 20,
    ) -> list[dict]:
        """Re-rank documents by query relevance using a local cross-encoder.

        Each document dict must have at minimum: {"id": str, "text": str}
        Additional fields (source_db, result_type, score, metadata) are preserved.

        Returns documents with `rerank_score` added, sorted by rerank_score desc.
        Falls back to original score ordering if the reranker is disabled or fails.
        """
        if not self._enabled or not documents:
            return sorted(documents, key=lambda d: d.get("score", 0), reverse=True)

        try:
            return await asyncio.to_thread(self._rerank_sync, query, documents, top_k)
        except Exception:
            logger.warning("Reranker failed, falling back to original scores", exc_info=True)
            return sorted(documents, key=lambda d: d.get("score", 0), reverse=True)[:top_k]

    def _rerank_sync(self, query: str, documents: list[dict], top_k: int) -> list[dict]:
        # Build the text list, skipping empty-text docs (with common-field fallback).
        texts: list[str] = []
        text_idx_to_doc_idx: list[int] = []
        for i, doc in enumerate(documents):
            text = doc.get("text", "")
            if not text:
                text = (
                    doc.get("fact_text", "")
                    or doc.get("title", "")
                    or doc.get("canonical_name", "")
                    or doc.get("summary", "")
                    or doc.get("content", "")
                    or ""
                )
            if not text:
                continue
            text_idx_to_doc_idx.append(i)
            texts.append(text)

        if not texts:
            return documents

        # fastembed yields one relevance score per document, in input order.
        scores = list(self._get_model().rerank(query, texts))
        scored_docs = []
        for j, score in enumerate(scores):
            doc = dict(documents[text_idx_to_doc_idx[j]])
            doc["rerank_score"] = float(score)
            scored_docs.append(doc)

        scored_docs.sort(key=lambda d: d.get("rerank_score", 0), reverse=True)
        return scored_docs[:top_k]
