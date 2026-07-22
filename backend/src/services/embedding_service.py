"""Embedding Service — generate vector embeddings with a local ONNX model.

Runs entirely on-host via fastembed (onnxruntime, no torch, no external API).
Default model: BAAI/bge-base-en-v1.5 (768-dim, MIT). The model is downloaded once
to the fastembed cache and loaded lazily on first use (pre-download at deploy so the
first request does not stall).
"""

import asyncio
import logging
import threading

from fastembed import TextEmbedding

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Output dimension of the default model (BAAI/bge-base-en-v1.5). Must match
# vector_store.VECTOR_SIZE and the Qdrant collection dimension.
EMBEDDING_DIM = 768


class EmbeddingService:
    """Generate embeddings via a local fastembed cross-platform ONNX model."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._model_name = settings.embedding_model
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    def _get_model(self) -> TextEmbedding:
        """Lazily construct the model once and reuse it (thread-safe singleton).

        fastembed models are read-only at inference, so a single instance is safe
        to call from multiple ``to_thread`` workers.
        """
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    async def embed_text(self, text: str) -> list[float] | None:
        """Generate an embedding vector for a single text string."""
        if not text or not text.strip():
            return None
        results = await self._embed_batch([text])
        if results is None:
            return None
        return results[0] if results else None

    async def embed_texts(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embedding vectors for multiple texts."""
        if not texts:
            return []
        return await self._embed_batch(texts)

    async def _embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        try:
            return await asyncio.to_thread(self._embed_sync, texts)
        except Exception:
            logger.warning("Embedding generation failed", exc_info=True)
            return None

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        # fastembed yields numpy arrays in input order; convert to plain floats.
        return [vec.tolist() for vec in model.embed(texts)]
