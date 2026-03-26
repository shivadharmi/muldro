"""Bedrock Reranker Service — neural re-ranking of search results.

Uses AWS Bedrock's amazon.rerank-v1:0 model to re-score search results
by cross-attention relevance to the query. This provides much better
relevance scoring than raw cosine similarity or BM25.
"""

import asyncio
import logging

from src.config.settings import Settings

logger = logging.getLogger(__name__)


class RerankerService:
    """Re-rank search results using AWS Bedrock Rerank API."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._region = settings.reranker_region
        self._model_id = settings.reranker_model
        self._enabled = settings.reranker_enabled
        self._client = None

    def _get_client(self):
        """Lazy-init boto3 Bedrock Agent Runtime client (cached)."""
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-agent-runtime", region_name=self._region)
        return self._client

    async def rerank(
        self,
        query: str,
        documents: list[dict],
        top_k: int = 20,
    ) -> list[dict]:
        """Re-rank documents by query relevance using Bedrock Rerank.

        Each document dict must have at minimum: {"id": str, "text": str}
        Additional fields (source_db, result_type, score, metadata) are preserved.

        Returns documents with `rerank_score` added, sorted by rerank_score desc.
        Falls back to original score ordering if reranker is unavailable.
        """
        if not self._enabled or not documents:
            return sorted(documents, key=lambda d: d.get("score", 0), reverse=True)

        try:
            reranked = await asyncio.to_thread(self._invoke_rerank, query, documents, top_k)
            return reranked
        except Exception:
            logger.warning(
                "Bedrock reranker failed, falling back to original scores", exc_info=True
            )
            return sorted(documents, key=lambda d: d.get("score", 0), reverse=True)[:top_k]

    def _resolve_model_arn(self) -> str:
        """Resolve the model ID to a full ARN if needed.

        Bedrock Rerank API requires the full ARN format:
        arn:aws:bedrock:<region>::foundation-model/<model-id>
        """
        model = self._model_id
        if model.startswith("arn:"):
            return model
        return f"arn:aws:bedrock:{self._region}::foundation-model/{model}"

    def _invoke_rerank(self, query: str, documents: list[dict], top_k: int) -> list[dict]:
        """Synchronous call to Bedrock Rerank API."""
        client = self._get_client()

        # Build the sources list for the Bedrock API
        sources = []
        for doc in documents:
            text = doc.get("text", "")
            if not text:
                # Try common text fields
                text = (
                    doc.get("fact_text", "")
                    or doc.get("title", "")
                    or doc.get("canonical_name", "")
                    or doc.get("summary", "")
                    or doc.get("content", "")
                    or ""
                )
            sources.append(
                {
                    "type": "INLINE",
                    "inlineDocumentSource": {
                        "type": "TEXT",
                        "textDocument": {"text": text},
                    },
                }
            )

        if not sources:
            return documents

        response = client.rerank(
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "modelConfiguration": {"modelArn": self._resolve_model_arn()},
                    "numberOfResults": min(top_k, len(documents)),
                },
            },
            sources=sources,
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
        )

        # Map rerank scores back to documents
        results = response.get("results", [])
        scored_docs = []
        for result in results:
            idx = result["index"]
            if 0 <= idx < len(documents):
                doc = dict(documents[idx])
                doc["rerank_score"] = result["relevanceScore"]
                scored_docs.append(doc)

        # Sort by rerank_score descending
        scored_docs.sort(key=lambda d: d.get("rerank_score", 0), reverse=True)
        return scored_docs
