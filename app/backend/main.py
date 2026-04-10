from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes import router as api_router, limiter, search_service
from app.core.config import get_settings, migrate_legacy_data_if_needed
from app.db.database import initialize_database

settings = get_settings()


def _resolve_asset_path(asset_path: str) -> Path | None:
  normalized = asset_path.strip('/').replace('\\', '/')
  if not normalized:
    return None

  if normalized.startswith('qrcodes/'):
    candidate = settings.public_dir / normalized
    return candidate if candidate.is_file() else None

  frontend_candidate = settings.frontend_assets_dir / normalized
  if frontend_candidate.is_file():
    return frontend_candidate

  public_candidate = settings.public_dir / normalized
  if public_candidate.is_file():
    return public_candidate

  return None


def _resolve_frontend_path(requested_path: str) -> Path | None:
  normalized = requested_path.strip('/')
  if not normalized:
    return settings.frontend_index_path if settings.frontend_index_path.is_file() else None

  candidate = settings.frontend_dist_dir / normalized
  if candidate.is_file():
    return candidate

  if candidate.is_dir():
    index_candidate = candidate / 'index.html'
    if index_candidate.is_file():
      return index_candidate

  if settings.frontend_index_path.is_file():
    return settings.frontend_index_path

  return None


@asynccontextmanager
async def lifespan(_: FastAPI):
  migrate_legacy_data_if_needed(settings)
  initialize_database(settings.database_path)
  settings.public_dir.mkdir(parents=True, exist_ok=True)
  settings.qrcode_dir.mkdir(parents=True, exist_ok=True)
  settings.viewed_dir.mkdir(parents=True, exist_ok=True)
  settings.viewed_qrcode_dir.mkdir(parents=True, exist_ok=True)
  try:
    yield
  finally:
    search_service.close()


app = FastAPI(
  title=settings.app_name,
  lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
  CORSMiddleware,
  allow_origins=list(settings.cors_origins),
  allow_credentials=True,
  allow_methods=['*'],
  allow_headers=['*'],
)

app.include_router(api_router)


@app.get('/assets/{asset_path:path}', include_in_schema=False)
def serve_asset(asset_path: str) -> FileResponse:
  asset = _resolve_asset_path(asset_path)
  if asset is None:
    raise HTTPException(status_code=404, detail='asset not found')
  return FileResponse(asset)


@app.get('/{full_path:path}', include_in_schema=False)
def serve_frontend(full_path: str) -> FileResponse:
  if full_path.startswith('api/'):
    raise HTTPException(status_code=404, detail='not found')

  frontend_file = _resolve_frontend_path(full_path)
  if frontend_file is None:
    raise HTTPException(status_code=404, detail='frontend not built')

  return FileResponse(frontend_file)
