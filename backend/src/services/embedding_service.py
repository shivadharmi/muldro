"""Embedding Service — generate and manage vector embeddings.

Uses AWS Bedrock Titan Text Embeddings V2 for vector generation.
Supports semantic search across memories, entities, and events.
"""

import asyncio
import json
import logging

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Titan Text Embeddings V2 supports 256, 512, or 1024 dimensions
EMBEDDING_DIM = 1024


class EmbeddingService:
    """Generate embeddings via AWS Bedrock Titan Text Embeddings V2."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._region = settings.bedrock_region
        self._model_id = settings.embedding_model

    def _get_client(self):
        """Create a boto3 Bedrock Runtime client (sync)."""
        import boto3

        return boto3.client("bedrock-runtime", region_name=self._region)

    async def embed_text(self, text: str) -> list[float] | None:
        """Generate an embedding vector for a single text string."""
        results = await self._embed_batch([text])
        if results is None:
            return None
        return results[0] if results else None

    async def embed_texts(self, texts: list[str]) -> list[list[float]] | None:
        """Generate embedding vectors for multiple texts."""
        if not texts:
            return []
        # Titan accepts one text at a time, so we batch sequentially
        results = []
        for text in texts:
            vec = await self.embed_text(text)
            if vec is None:
                return None
            results.append(vec)
        return results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        """Call Bedrock Titan for each text, with retry."""
        max_retries = 3
        results = []
        for text in texts:
            for attempt in range(max_retries + 1):
                try:
                    vec = await asyncio.to_thread(self._invoke_titan, text)
                    results.append(vec)
                    break
                except Exception:
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                    logger.warning("Embedding generation failed", exc_info=True)
                    return None
        return results

    def _invoke_titan(self, text: str) -> list[float]:
        """Synchronous call to Bedrock Titan Text Embeddings V2."""
        client = self._get_client()
        body = json.dumps(
            {
                "inputText": text,
                "dimensions": EMBEDDING_DIM,
                "normalize": True,
            }
        )
        response = client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        result = json.loads(response["body"].read())
        return result["embedding"]
