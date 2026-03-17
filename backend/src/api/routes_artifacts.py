"""Artifact CRUD routes — metadata in Postgres, content in S3."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from src.api.deps import get_current_user_id, get_current_workspace_id, get_session
from src.config.settings import Settings, get_settings
from src.models.artifacts import Artifact
from src.services.artifact_store import ArtifactStore

router = APIRouter()
logger = logging.getLogger(__name__)


class ArtifactItem(BaseModel):
    artifact_id: str
    artifact_type: str
    title: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    source_ref: dict | None = None
    entity_links: list[str] | None = None
    metadata_: dict | None = None
    created_at: str | None = None

    model_config = {"from_attributes": True}


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactItem]


class ArtifactCreateRequest(BaseModel):
    artifact_type: str
    title: str | None = None
    mime_type: str = "application/octet-stream"
    content_base64: str | None = None
    source_ref: dict | None = None
    entity_links: list[str] | None = None
    metadata_: dict | None = None


def _to_item(a: Artifact) -> ArtifactItem:
    return ArtifactItem(
        artifact_id=a.artifact_id,
        artifact_type=a.artifact_type,
        title=a.title,
        mime_type=a.mime_type,
        size_bytes=a.size_bytes,
        source_ref=a.source_ref,
        entity_links=a.entity_links,
        metadata_=a.metadata_,
        created_at=a.created_at.isoformat() if a.created_at else None,
    )


@router.get("/v1/artifacts", response_model=ArtifactListResponse)
async def list_artifacts(
    artifact_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """List artifacts for the current user."""
    stmt = select(Artifact).where(
        Artifact.user_id == user_id, Artifact.workspace_id == workspace_id
    )

    if artifact_type:
        stmt = stmt.where(Artifact.artifact_type == artifact_type)

    stmt = stmt.order_by(Artifact.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return ArtifactListResponse(artifacts=[_to_item(a) for a in rows])


@router.get("/v1/artifacts/{artifact_id}", response_model=ArtifactItem)
async def get_artifact(
    artifact_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
):
    """Get artifact metadata by ID."""
    result = await db.execute(
        select(Artifact).where(
            Artifact.artifact_id == artifact_id,
            Artifact.user_id == user_id,
            Artifact.workspace_id == workspace_id,
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
    return _to_item(artifact)


@router.get("/v1/artifacts/{artifact_id}/content")
async def get_artifact_content(
    artifact_id: str,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Get artifact content from S3."""
    result = await db.execute(
        select(Artifact).where(
            Artifact.artifact_id == artifact_id,
            Artifact.user_id == user_id,
            Artifact.workspace_id == workspace_id,
        )
    )
    artifact = result.scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")

    store = ArtifactStore(settings)
    try:
        content = await store.retrieve(artifact.s3_key)
    except Exception:
        logger.exception("Failed to retrieve artifact content: %s", artifact.s3_key)
        raise HTTPException(status_code=502, detail="Failed to retrieve artifact content")

    return Response(
        content=content,
        media_type=artifact.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{artifact.title or artifact_id}"'},
    )


@router.post("/v1/artifacts", response_model=ArtifactItem, status_code=201)
async def create_artifact(
    req: ArtifactCreateRequest,
    user_id: str = Depends(get_current_user_id),
    workspace_id: str = Depends(get_current_workspace_id),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    """Create a new artifact. Stores content in S3 and metadata in Postgres."""
    import base64

    artifact_id = f"art_{ULID()}"

    # Store content in S3 if provided
    content_bytes = b""
    if req.content_base64:
        content_bytes = base64.b64decode(req.content_base64)

    store = ArtifactStore(settings)
    s3_key = await store.store(
        user_id=user_id,
        artifact_type=req.artifact_type,
        content=content_bytes,
        mime_type=req.mime_type,
        metadata={"artifact_id": artifact_id},
    )

    artifact = Artifact(
        artifact_id=artifact_id,
        user_id=user_id,
        workspace_id=workspace_id,
        artifact_type=req.artifact_type,
        title=req.title,
        mime_type=req.mime_type,
        size_bytes=len(content_bytes),
        s3_key=s3_key,
        s3_bucket=settings.s3_bucket or "",
        source_ref=req.source_ref,
        entity_links=req.entity_links,
        metadata_=req.metadata_,
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return _to_item(artifact)
