from datetime import datetime, timezone

from fastapi import APIRouter

from app.api.schemas import HealthResponse, SearchRequest, SearchResponse
from app.core.config import get_settings
from app.search import SearchService

router = APIRouter(prefix='/api', tags=['system'])
settings = get_settings()
search_service = SearchService(settings)


@router.get('/health', response_model=HealthResponse)
def healthcheck() -> HealthResponse:
  chromium_ready = any(
    settings.playwright_install_dir.glob('chromium-*/chrome-win/chrome.exe'),
  )

  return HealthResponse(
    status='ok',
    service='ai-group-discovery-api',
    app_name=settings.app_name,
    database_path=str(settings.database_path),
    chromium_ready=chromium_ready,
    timestamp=datetime.now(timezone.utc),
  )


@router.post('/search', response_model=SearchResponse)
def search(request: SearchRequest) -> SearchResponse:
  results = search_service.search(request.query)

  return SearchResponse(
    query=request.query,
    results=results,
    empty_message='未发现该产品的官方群' if not results else None,
  )
