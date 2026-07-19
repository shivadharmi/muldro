"""Embedding Service — generate and manage vector embeddings.

Primary: Voyage AI (httpx) when JARVIS_VOYAGE_API_KEY is set.
Fallback: AWS Bedrock Titan Text Embeddings V2.
"""

import asyncio
import json
import logging

import httpx

from src.config.settings import Settings

logger = logging.getLogger(__name__)

# Titan Text Embeddings V2 supports 256, 512, or 1024 dimensions
EMBEDDING_DIM = 1024


class EmbeddingService:
    """Generate embeddings via Voyage AI or AWS Bedrock Titan Text Embeddings V2."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._region = settings.bedrock_region
        self._model_id = settings.embedding_model
        self._voyage_key = settings.voyage_api_key
        self._voyage_url = settings.voyage_base_url.rstrip("/")
        self._use_voyage = bool(self._voyage_key)

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
        results = await self._embed_batch(texts)
        return results

    async def _embed_batch(self, texts: list[str]) -> list[list[float]] | None:
        if self._use_voyage:
            return await self._embed_voyage(texts)
        return await self._embed_titan(texts)

    # --- Voyage AI ---

    async def _embed_voyage(self, texts: list[str]) -> list[list[float]] | None:
        url = f"{self._voyage_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._voyage_key}",
            "Content-Type": "application/json",
        }
        payload = {"input": texts, "model": self._model_id}
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    return [item["embedding"] for item in data["data"]]
            except httpx.HTTPStatusError as e:
                # 4xx (unsupported model, malformed request) is permanent — retrying
                # wastes ~14s of backoff and never succeeds. Log the response body so
                # the cause is visible (e.g. "Model voyage-3 is not supported").
                body = e.response.text[:500]
                if e.response.status_code < 500:
                    logger.warning(
                        "Voyage embedding rejected (%s): %s", e.response.status_code, body
                    )
                    return None
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                logger.warning(
                    "Voyage embedding failed after retries (%s): %s",
                    e.response.status_code,
                    body,
                )
                return None
            except Exception:
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                logger.warning("Voyage embedding generation failed", exc_info=True)
                return None

    # --- Bedrock Titan (fallback) ---

    def _get_bedrock_client(self):
        import boto3

        return boto3.client("bedrock-runtime", region_name=self._region)

    async def _embed_titan(self, texts: list[str]) -> list[list[float]] | None:
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
        client = self._get_bedrock_client()
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
