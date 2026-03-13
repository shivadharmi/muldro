"""Embedding Service — generate and manage vector embeddings.

Wraps the Anthropic/Voyage embedding API to produce vectors for
semantic search across memories, entities, and events.
"""

import asyncio
import logging

import httpx

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Default embedding dimension (voyage-3.5-lite = 1024)
EMBEDDING_DIM = 1024


class EmbeddingService:
    """Generate embeddings for text content."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._api_key = settings.voyage_api_key or settings.anthropic_api_key
        self._model = settings.embedding_model
        self._base_url = settings.voyage_base_url

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
        """Call embedding API for a batch of texts, with retry on 429."""
        max_retries = 3
        for attempt in range(max_retries + 1):
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
                    if response.status_code == 429 and attempt < max_retries:
                        delay = 2 ** (attempt + 1)
                        logger.info("Embedding rate limited, retrying in %ds", delay)
                        await asyncio.sleep(delay)
                        continue
                    response.raise_for_status()
                    data = response.json()
                    embeddings = [item["embedding"] for item in data["data"]]
                    if return_all:
                        return embeddings
                    return embeddings[0] if embeddings else None
            except Exception:
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                logger.warning("Embedding generation failed", exc_info=True)
                return None
        return None
