"""Embedding Service — generate and manage vector embeddings.

Wraps the Anthropic/Voyage embedding API to produce vectors for
semantic search across memories, entities, and events.
"""

import logging

import httpx

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Default embedding dimension for voyage-3-lite
EMBEDDING_DIM = 1536


class EmbeddingService:
    """Generate embeddings for text content."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._api_key = settings.voyage_api_key or settings.anthropic_api_key
        self._model = settings.embedding_model
        self._base_url = "https://api.voyageai.com/v1"

    async def embed_text(self, text: str) -> list[float] | None:
        """Generate an embedding vector for a single text string."""
        return await self._embed_batch([text])

    async def embed_texts(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embedding vectors for multiple texts."""
        if not texts:
            return []
        results = []
        # Batch in groups of 128 (Voyage API limit)
        for i in range(0, len(texts), 128):
            batch = texts[i : i + 128]
            batch_result = await self._embed_batch(batch, return_all=True)
            if batch_result is None:
                return None
            results.extend(batch_result)
        return results

    async def _embed_batch(
        self, texts: list[str], return_all: bool = False
    ) -> list[float] | list[list[float]] | None:
        """Call embedding API for a batch of texts."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._base_url}/embeddings",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "input": texts,
                        "input_type": "document",
                    },
                )
                response.raise_for_status()
                data = response.json()
                embeddings = [item["embedding"] for item in data["data"]]
                if return_all:
                    return embeddings
                return embeddings[0] if embeddings else None
        except Exception:
            logger.warning("Embedding generation failed", exc_info=True)
            return None
