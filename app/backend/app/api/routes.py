from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api.schemas import (
  BulkMarkViewedRequest,
  BulkMarkViewedResponse,
  GroupType,
  HealthResponse,
  MarkViewedGroupRequest,
  MarkViewedGroupResponse,
  ManualUploadResponse,
  Platform,
  RemoveViewedGroupResponse,
  RecommendationsResponse,
  SearchRequest,
  SearchResponse,
  ToggleJoinedResponse,
  ViewedGroupsResponse,
)
from app.core.config import get_settings
from app.search import SearchService

limiter = Limiter(key_func=get_remote_address)
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
    service='ai-tool-discovery-api',
    app_name=settings.app_name,
    database_path=str(settings.database_path),
    chromium_ready=chromium_ready,
    timestamp=datetime.now(timezone.utc),
  )


@router.get('/recommendations', response_model=RecommendationsResponse)
def get_recommendations(refresh: bool = False) -> RecommendationsResponse:
  return search_service.get_recommendations(force_refresh=refresh)


@router.post('/search', response_model=SearchResponse)
@limiter.limit('10/minute')
def search(request: Request, search_request: SearchRequest) -> SearchResponse:
  results = search_service.search(
    search_request.query,
    search_request.filters,
    refresh=search_request.refresh,
    limit=search_request.limit,
  )

  return SearchResponse(
    query=search_request.query,
    results=results,
    empty_message='未在 GitHub/官网相关页面中发现官方群入口' if not results else None,
  )

@router.get('/groups/viewed', response_model=ViewedGroupsResponse)
def list_viewed_groups() -> ViewedGroupsResponse:
  return ViewedGroupsResponse(groups=search_service.list_viewed_groups())


@router.post('/groups/viewed', response_model=MarkViewedGroupResponse)
def mark_group_viewed(payload: MarkViewedGroupRequest) -> MarkViewedGroupResponse:
  search_service.mark_group_viewed(
    product_id=payload.product_id,
    app_name=payload.app_name,
    group=payload.group,
  )
  return MarkViewedGroupResponse(ok=True)


@router.post('/groups/viewed/bulk', response_model=BulkMarkViewedResponse)
def bulk_mark_viewed(payload: BulkMarkViewedRequest) -> BulkMarkViewedResponse:
  count = search_service.bulk_mark_viewed(payload.items)
  return BulkMarkViewedResponse(ok=True, count=count)


@router.patch('/groups/viewed/{view_key}/joined', response_model=ToggleJoinedResponse)
def toggle_group_joined(view_key: str) -> ToggleJoinedResponse:
  is_joined = search_service.toggle_group_joined(view_key)
  return ToggleJoinedResponse(ok=True, is_joined=is_joined)


@router.post('/groups/manual-upload', response_model=ManualUploadResponse)
async def manual_upload_group(
  app_name: str = Form(...),
  description: str | None = Form(None),
  created_at: str | None = Form(None),
  github_stars: int | None = Form(None),
  platform: str = Form(...),
  group_type: str = Form(GroupType.UNKNOWN.value),
  entry_type: str = Form(...),
  entry_url: str | None = Form(None),
  fallback_url: str | None = Form(None),
  qrcode_file: UploadFile | None = File(None),
) -> ManualUploadResponse:
  try:
    platform_value = Platform(platform)
  except ValueError as exc:
    raise HTTPException(status_code=400, detail='invalid platform') from exc

  try:
    group_type_value = GroupType(group_type)
  except ValueError as exc:
    raise HTTPException(status_code=400, detail='invalid group_type') from exc

  qrcode_bytes = await qrcode_file.read() if qrcode_file is not None else None
  qrcode_content_type = qrcode_file.content_type if qrcode_file is not None else None

  try:
    view_key = search_service.manual_upload_group(
      app_name=app_name,
      description=description,
      created_at=created_at,
      github_stars=github_stars,
      platform=platform_value,
      group_type=group_type_value,
      entry_type=entry_type,
      entry_url=entry_url,
      fallback_url=fallback_url,
      qrcode_bytes=qrcode_bytes,
      qrcode_content_type=qrcode_content_type,
    )
  except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc

  return ManualUploadResponse(ok=True, view_key=view_key)


@router.delete('/groups/viewed/{view_key}', response_model=RemoveViewedGroupResponse)
def remove_viewed_group(view_key: str) -> RemoveViewedGroupResponse:
  search_service.remove_viewed_group(view_key)
  return RemoveViewedGroupResponse(ok=True)
