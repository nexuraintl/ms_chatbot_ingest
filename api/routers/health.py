from fastapi import APIRouter

from api.core.config import get_settings

router = APIRouter(tags=["infra"])


@router.get("/health")
async def health() -> dict:
    return {"status": "UP"}


@router.get("/version")
async def version() -> dict:
    settings = get_settings()
    return {
        "service": settings.service_name,
        "version": settings.service_version,
        "environment": settings.environment,
    }
