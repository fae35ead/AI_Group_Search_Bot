from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.routes import router as api_router, limiter
from app.core.config import get_settings
from app.db.database import initialize_database

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
  initialize_database(settings.database_path)
  settings.public_dir.mkdir(parents=True, exist_ok=True)
  settings.qrcode_dir.mkdir(parents=True, exist_ok=True)
  settings.viewed_dir.mkdir(parents=True, exist_ok=True)
  settings.viewed_qrcode_dir.mkdir(parents=True, exist_ok=True)
  yield


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
app.mount(
  '/assets',
  StaticFiles(directory=settings.public_dir, check_dir=False),
  name='assets',
)
