"""GET /health and GET /version."""

from fastapi import APIRouter, Request, Response

from yorath import __version__
from yorath.distribution.health import run_checks
from yorath.distribution.manifest import Manifest

router = APIRouter(tags=["distribution"])


@router.get("/health")
async def health(request: Request, response: Response) -> dict:
    result = await run_checks(request.app.state.checks)
    if result["status"] != "ok":
        response.status_code = 503
    return result


@router.get("/version")
async def version(request: Request) -> dict:
    manifest: Manifest = request.app.state.manifest
    schema_head: str | None = request.app.state.schema_head
    return {
        "distribution": manifest.distribution.model_dump(),
        "core": __version__,
        "build": request.app.state.build_sha,
        "schema_head": schema_head,
        "schema_matches_manifest": schema_head == manifest.distribution.schema_version,
        "images": manifest.images.model_dump(),
        "runtimes": manifest.runtimes.model_dump(),
        "models": {
            "embeddings": manifest.models.embeddings,
            "default_cpu": manifest.models.default_cpu,
            "default_gpu": manifest.models.default_gpu,
        },
        "plugins": request.app.state.plugins,
    }
