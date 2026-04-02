import base64
import hashlib
import imghdr
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlparse

import cv2
import httpx
import numpy as np
from bs4 import BeautifulSoup, Tag

from app.api.schemas import GroupType, Platform
from app.core.config import Settings
from app.search.group_type_classifier import GroupTypeClassifier
from app.search.models import ExtractedGroupCandidate, ExtractionStats, FetchedPage


class EntryExtractor:
  def __init__(self, settings: Settings):
    self.settings = settings
    self.group_type_classifier = GroupTypeClassifier()

  def extract(
    self,
    pages: list[FetchedPage],
    stats: ExtractionStats | None = None,
  ) -> list[ExtractedGroupCandidate]:
    groups: list[ExtractedGroupCandidate] = []
    seen: set[str] = set()

    for page in pages:
      soup = BeautifulSoup(page.html, 'html.parser')
      tags = soup.find_all(['a', 'img'])

      if stats is not None:
        stats.scanned_tags += len(tags)

      for tag in tags:
        candidate = self._extract_candidate(tag, page.final_url, stats)

        if candidate is None:
          continue

        signature = '|'.join(
          filter(None, [candidate.platform.value, candidate.image_url, candidate.entry_url, candidate.source_url]),
        )

        if signature in seen:
          continue

        seen.add(signature)
        groups.append(candidate)

        if stats is not None:
          stats.output_candidates += 1

    return groups

  def _extract_candidate(
    self,
    tag: Tag,
    page_url: str,
    stats: ExtractionStats | None,
  ) -> ExtractedGroupCandidate | None:
    context = self._extract_context(tag)
    lowered = context.lower()

    if not self._has_positive_context(lowered):
      if stats is not None:
        stats.filtered_positive_context += 1
      return None

    if self._is_negative_context(lowered):
      if stats is not None:
        stats.filtered_negative_context += 1
      return None

    if stats is not None:
      stats.contextual_candidates += 1

    group_type = self.group_type_classifier.classify(context)

    if tag.name == 'a':
      return self._extract_link_candidate(tag, page_url, context, group_type, stats)

    return self._extract_image_candidate(tag, page_url, context, group_type, stats)

  def _extract_link_candidate(
    self,
    tag: Tag,
    page_url: str,
    context: str,
    group_type: GroupType,
    stats: ExtractionStats | None,
  ) -> ExtractedGroupCandidate | None:
    if tag.find('img') is not None:
      return None

    href = tag.get('href')

    if not href:
      return None

    absolute = urljoin(page_url, href)
    platform = self._detect_platform(absolute, context)

    if platform is None:
      if stats is not None:
        stats.filtered_platform_failure += 1
      return None

    return ExtractedGroupCandidate(
      platform=platform,
      group_type=group_type,
      source_url=page_url,
      context=context,
      entry_url=absolute,
      source_urls=[page_url],
    )

  def _extract_image_candidate(
    self,
    tag: Tag,
    page_url: str,
    context: str,
    group_type: GroupType,
    stats: ExtractionStats | None,
  ) -> ExtractedGroupCandidate | None:
    src = self._resolve_image_source(tag)

    if not src:
      return None

    image_url = urljoin(page_url, src)
    content = self._download_image(image_url)

    if content is None:
      return None

    image_bytes, content_type = content
    decoded_payload, is_qrcode = self._decode_qrcode(image_bytes)

    if not is_qrcode:
      if stats is not None:
        stats.filtered_qrcode_failure += 1
      return None

    platform = self._detect_platform(decoded_payload or image_url, context)

    if platform is None:
      if stats is not None:
        stats.filtered_platform_failure += 1
      return None

    stored_path = self._store_image(image_bytes, content_type, image_url)
    fallback_url = self._find_nearest_link(tag, page_url)

    return ExtractedGroupCandidate(
      platform=platform,
      group_type=group_type,
      source_url=page_url,
      context=context,
      image_url=stored_path,
      image_bytes=image_bytes,
      image_content_type=content_type,
      fallback_url=fallback_url,
      decoded_payload=decoded_payload,
      source_urls=[page_url],
    )

  def _extract_context(self, tag: Tag) -> str:
    pieces: list[str] = []
    current: Tag | None = tag

    for _ in range(3):
      if current is None:
        break

      pieces.append(current.get_text(' ', strip=True))
      current = current.parent if isinstance(current.parent, Tag) else None

    return ' '.join(filter(None, pieces))

  def _has_positive_context(self, text: str) -> bool:
    positive_keywords = (
      '群',
      '社群',
      '社区',
      '交流群',
      '官方群',
      '答疑',
      '开发者群',
      '微信',
      '微信群',
      'qq群',
      '飞书群',
      '二维码',
      '扫码',
      'community',
      'group',
      'join',
      'invite',
      'wechat',
      'weixin',
      'qq',
      'feishu',
      'lark',
      'support',
    )
    return any(keyword in text for keyword in positive_keywords)

  def _is_negative_context(self, text: str) -> bool:
    negative_keywords = (
      '联系我们',
      'contact us',
      'contact',
      '公众号',
      'official account',
      'newsletter',
      'discord',
      'telegram',
      '钉钉',
    )
    return any(keyword in text for keyword in negative_keywords) and '群' not in text

  def _detect_platform(self, source: str, context: str) -> Platform | None:
    haystack = f'{source} {context}'.lower()

    if any(token in haystack for token in ('weixin', 'wechat', '微信')):
      return Platform.WECHAT

    if self._looks_like_qq_group(source, context):
      return Platform.QQ

    if any(token in haystack for token in ('feishu', 'larksuite', '飞书', 'lark')):
      return Platform.FEISHU

    return None

  def _looks_like_qq_group(self, source: str, context: str) -> bool:
    lowered_source = source.lower()
    lowered_context = context.lower()

    if any(token in lowered_source for token in ('qm.qq.com', 'qun.qq.com', 'jq.qq.com')):
      return True

    patterns = (
      r'qq\s*官方群',
      r'qq\s*群',
      r'加入\s*qq\s*群',
      r'qq群',
      r'qq群聊',
    )

    return any(re.search(pattern, lowered_context) for pattern in patterns)

  def _download_image(self, image_url: str) -> tuple[bytes, str] | None:
    if image_url.startswith('data:image/'):
      header, encoded = image_url.split(',', 1)
      mime = header.split(';', 1)[0].split(':', 1)[1]
      return base64.b64decode(encoded), mime

    try:
      with httpx.Client(
        headers={'User-Agent': self.settings.user_agent},
        follow_redirects=True,
        timeout=self.settings.request_timeout_seconds,
      ) as client:
        response = client.get(image_url)
        response.raise_for_status()
    except httpx.HTTPError:
      return None

    return response.content, response.headers.get('content-type', 'image/png')

  def _decode_qrcode(self, image_bytes: bytes) -> tuple[str | None, bool]:
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

    if image is None:
      return None, False

    detector = cv2.QRCodeDetector()
    decoded_text, points, _ = detector.detectAndDecode(image)

    if points is not None:
      return decoded_text or None, True

    try:
      detected, decoded_info, points_multi, _ = detector.detectAndDecodeMulti(image)
    except Exception:
      return None, False

    if detected and points_multi is not None:
      joined = ' '.join(item for item in decoded_info if item)
      return joined or None, True

    return None, False

  def _store_image(self, image_bytes: bytes, content_type: str, image_url: str) -> str:
    extension = self._guess_extension(image_bytes, content_type, image_url)
    digest = hashlib.sha1(image_bytes).hexdigest()
    filename = f'{digest}.{extension}'
    destination = self.settings.qrcode_dir / filename
    destination.write_bytes(image_bytes)
    return f'/assets/qrcodes/{filename}'

  def _guess_extension(self, image_bytes: bytes, content_type: str, image_url: str) -> str:
    if 'png' in content_type:
      return 'png'

    if 'jpeg' in content_type or 'jpg' in content_type:
      return 'jpg'

    detected = imghdr.what(None, image_bytes)

    if detected:
      return 'jpg' if detected == 'jpeg' else detected

    parsed = urlparse(image_url)
    suffix = Path(parsed.path).suffix.lower().lstrip('.')
    return suffix or 'png'

  def _find_nearest_link(self, tag: Tag, page_url: str) -> str | None:
    current: Tag | None = tag

    for _ in range(3):
      if current is None:
        break

      anchor = current if current.name == 'a' else current.find('a', href=True)

      if anchor and anchor.get('href'):
        return urljoin(page_url, anchor['href'])

      current = current.parent if isinstance(current.parent, Tag) else None

    return None

  def _resolve_image_source(self, tag: Tag) -> str | None:
    for attribute in ('src', 'data-src'):
      value = tag.get(attribute)

      if value:
        return value

    srcset = tag.get('srcset')

    if not srcset:
      return None

    entries = [entry.strip() for entry in srcset.split(',') if entry.strip()]

    for entry in reversed(entries):
      parts = entry.split()

      if parts and parts[0]:
        return parts[0]

    return None
