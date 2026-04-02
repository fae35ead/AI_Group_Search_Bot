from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Platform(StrEnum):
  WECHAT = '微信'
  QQ = 'QQ'
  FEISHU = '飞书'


class GroupType(StrEnum):
  DISCUSSION = '交流群'
  QA = '答疑群'
  AFTER_SALES = '售后群'
  BETA = '招募/内测群'
  UNKNOWN = '未知'


class QRCodeEntry(BaseModel):
  type: Literal['qrcode']
  image_path: str
  fallback_url: str | None = None


class LinkEntry(BaseModel):
  type: Literal['link']
  url: str
  note: Literal['二维码暂未抓取成功'] = '二维码暂未抓取成功'


GroupEntry = Annotated[QRCodeEntry | LinkEntry, Field(discriminator='type')]


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
  groups: list[OfficialGroup]


class HealthResponse(BaseModel):
  status: Literal['ok']
  service: str
  app_name: str
  database_path: str
  chromium_ready: bool
  timestamp: datetime


class SearchRequest(BaseModel):
  query: str = Field(min_length=1, max_length=200)


class SearchResponse(BaseModel):
  query: str
  results: list[ProductCard]
  empty_message: str | None = None
