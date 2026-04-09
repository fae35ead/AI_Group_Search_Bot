from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
  min_stars: int | None = Field(default=None, ge=0)
  created_after: datetime | None = Field(default=None)
  created_before: datetime | None = Field(default=None)


class Platform(StrEnum):
  WECHAT = '微信'
  QQ = 'QQ'
  FEISHU = '飞书'
  DISCORD = 'Discord'
  WECOM = '\u4f01\u4e1a\u5fae\u4fe1'
  DINGTALK = '\u9489\u9489'


class GroupType(StrEnum):
  DISCUSSION = '交流群'
  QA = '答疑群'
  AFTER_SALES = '售后群'
  BETA = '招募/内测群'
  UNKNOWN = '未知'


class GroupDiscoveryStatus(StrEnum):
  FOUND = 'found'
  NOT_FOUND = 'not_found'


class QRCodeEntry(BaseModel):
  type: Literal['qrcode']
  image_path: str
  fallback_url: str | None = None


class LinkEntry(BaseModel):
  type: Literal['link']
  url: str
  note: Literal['二维码暂未抓取成功'] = '二维码暂未抓取成功'


class QQNumberEntry(BaseModel):
  type: Literal['qq_number']
  qq_number: str
  note: Literal['未发现二维码/链接，已提取QQ群号'] = '未发现二维码/链接，已提取QQ群号'


GroupEntry = Annotated[QRCodeEntry | LinkEntry | QQNumberEntry, Field(discriminator='type')]


class OfficialGroup(BaseModel):
  group_id: str
  platform: Platform
  group_type: GroupType
  entry: GroupEntry
  is_added: bool = False
  source_urls: list[str] = Field(default_factory=list)


class ProductCard(BaseModel):
  product_id: str
  app_name: str
  description: str
  github_stars: int | None = None
  created_at: datetime | None = None
  verified_at: datetime
  groups: list[OfficialGroup] = Field(default_factory=list)
  group_discovery_status: GroupDiscoveryStatus
  official_site_url: str | None = None
  github_repo_url: str | None = None


class HealthResponse(BaseModel):
  status: Literal['ok']
  service: str
  app_name: str
  database_path: str
  chromium_ready: bool
  timestamp: datetime


class SearchRequest(BaseModel):
  query: str = Field(min_length=1, max_length=200)
  filters: SearchFilters | None = None
  refresh: bool = False
  limit: int = Field(default=10, ge=3, le=50)


class SearchResponse(BaseModel):
  query: str
  results: list[ProductCard]
  empty_message: str | None = None


class MarkViewedGroupRequest(BaseModel):
  product_id: str
  app_name: str
  group: OfficialGroup


class MarkViewedGroupResponse(BaseModel):
  ok: Literal[True]


class RemoveViewedGroupResponse(BaseModel):
  ok: Literal[True]


class ManualUploadResponse(BaseModel):
  ok: Literal[True]
  view_key: str


class ViewedGroupItem(BaseModel):
  view_key: str
  product_id: str
  app_name: str
  platform: Platform
  group_type: GroupType
  entry: GroupEntry
  viewed_at: datetime
  is_joined: bool = False


class ViewedGroupsResponse(BaseModel):
  groups: list[ViewedGroupItem]


class ToggleJoinedResponse(BaseModel):
  ok: Literal[True]
  is_joined: bool


class BulkMarkViewedRequest(BaseModel):
  items: list[MarkViewedGroupRequest]


class BulkMarkViewedResponse(BaseModel):
  ok: Literal[True]
  count: int


class RecommendedTool(BaseModel):
  name: str
  full_name: str
  stars: int
  description: str | None = None
  topics: list[str] = Field(default_factory=list)


class RecommendationsResponse(BaseModel):
  tools: list[RecommendedTool]
  cached_at: datetime
