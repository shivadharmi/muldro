"""Prometheus metrics endpoint."""

from fastapi import APIRouter
from fastapi.responses import Response

from src.services.metrics_service import MetricsService

router = APIRouter()


@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint."""
    data = MetricsService.generate_metrics()
    return Response(content=data, media_type="text/plain; version=0.0.4; charset=utf-8")
