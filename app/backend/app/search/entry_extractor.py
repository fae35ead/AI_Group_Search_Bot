import base64
import hashlib
import imghdr
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import cv2
import httpx
import numpy as np
from bs4 import BeautifulSoup, NavigableString, Tag

from app.api.schemas import GroupType, Platform
from app.core.config import Settings
from app.search.group_type_classifier import GroupTypeClassifier
from app.search.models import (
  ExtractedGroupCandidate,
  ExtractionStats,
  FetchedPage,
  PageExtractionSummary,
)

CONTEXT_BLOCK_TAGS = {'li', 'p', 'section', 'article', 'td', 'th', 'div'}
HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
STRONG_POSITIVE_KEYWORDS = (
  '社区交流群',
  '官方群',
  '飞书群',
  '微信群',
  'qq群',
  '扫码入群',
  '加群',
  '入群',
  '社群',
  '二维码',
)
WEAK_POSITIVE_KEYWORDS = (
  'community',
  'group',
  'join',
  'support',
  'invite',
  'wechat',
  'weixin',
  'qq',
  'feishu',
  'lark',
  '飞书',
  '微信',
)
NEGATIVE_KEYWORDS = (
  'contact us',
  'contact',
  'newsletter',
  'official account',
  'discord',
  'telegram',
  '钉钉',
  '公众号',
)
PLATFORM_HINTS = {
  Platform.WECHAT: ('weixin', 'wechat', '微信'),
  Platform.QQ: ('qm.qq.com', 'qun.qq.com', 'jq.qq.com', 'qq', 'qq群'),
  Platform.FEISHU: ('feishu', 'larksuite', '飞书', 'lark'),
}
QRCODE_URL_HINTS = ('qr', 'qrcode', 'wechat', 'weixin', 'qq', 'feishu', 'lark')


class EntryExtractor:
  def __init__(self, settings: Settings):
    self.settings = settings
    self.group_type_classifier = GroupTypeClassifier()

  def extract(
    self,
    pages: list[FetchedPage],
    stats: ExtractionStats | None = None,
  ) -> list[ExtractedGroupCandidate]:
    raw_candidates: list[ExtractedGroupCandidate] = []

    for page in pages:
      soup = BeautifulSoup(page.html, 'html.parser')
      tags = soup.find_all(['a', 'img'])
      page_summary = PageExtractionSummary(page_url=page.final_url)

      if stats is not None:
        stats.scanned_tags += len(tags)
      page_summary.scanned_tags += len(tags)

      for tag in tags:
        candidate = self._extract_candidate(tag, page.final_url, stats, page_summary)

        if candidate is None:
          continue

        raw_candidates.append(candidate)
        page_summary.output_candidates += 1

      if stats is not None:
        stats.page_summaries.append(page_summary)

    deduplicated = self._deduplicate_candidates(raw_candidates, stats)

    if stats is not None:
      stats.output_candidates = len(deduplicated)

    return deduplicated

  def _extract_candidate(
    self,
    tag: Tag,
    page_url: str,
    stats: ExtractionStats | None,
    page_summary: PageExtractionSummary,
  ) -> ExtractedGroupCandidate | None:
    context = self._extract_context(tag)
    lowered = context.lower()
    positive_level = self._positive_context_level(lowered)

    if positive_level == 0:
      if stats is not None:
        stats.filtered_positive_context += 1
      return None

    if self._is_negative_context(lowered) and positive_level < 2:
      if stats is not None:
        stats.filtered_negative_context += 1
      return None

    if stats is not None:
      stats.contextual_candidates += 1
    page_summary.contextual_candidates += 1

    group_type = self.group_type_classifier.classify(context)

    if tag.name == 'a':
      if stats is not None:
        stats.link_candidates += 1
      page_summary.link_candidates += 1
      return self._extract_link_candidate(
        tag,
        page_url,
        context,
        group_type,
        stats,
      )

    if stats is not None:
      stats.image_candidates += 1
    page_summary.image_candidates += 1
    return self._extract_image_candidate(
      tag,
      page_url,
      context,
      positive_level,
      group_type,
      stats,
    )

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

    if self._is_link_noise(tag, absolute, page_url):
      if stats is not None:
        stats.filtered_link_noise += 1
      return None

    if not self._link_has_platform_signal(absolute, context):
      if stats is not None:
        stats.filtered_link_noise += 1
      return None

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
    positive_level: int,
    group_type: GroupType,
    stats: ExtractionStats | None,
  ) -> ExtractedGroupCandidate | None:
    source = self._resolve_image_source(tag)

    if not source:
      return None

    image_url = urljoin(page_url, source)
    platform_source = self._unwrap_image_url(image_url)
    content = self._download_image(image_url)

    if content is None:
      if stats is not None:
        stats.image_download_failures += 1
      return None

    image_bytes, content_type = content
    decoded_payload, detected_qrcode = self._decode_qrcode(image_bytes)
    platform = self._detect_platform(decoded_payload, platform_source, context)
    looks_like_qrcode = self._looks_like_qrcode_image(
      image_bytes,
      platform_source,
      detected_qrcode=detected_qrcode,
    )

    if decoded_payload:
      if stats is not None:
        stats.image_decode_successes += 1
    elif looks_like_qrcode and positive_level >= 2:
      if stats is not None:
        stats.image_decode_fallbacks += 1
    else:
      if stats is not None:
        if detected_qrcode or looks_like_qrcode:
          stats.filtered_platform_failure += 1
        else:
          stats.filtered_qrcode_failure += 1
      return None

    if platform is None:
      if stats is not None:
        stats.filtered_platform_failure += 1
      return None

    stored_path = self._store_image(image_bytes, content_type, platform_source)
    fallback_url = self._find_nearest_link(tag, page_url)

    return ExtractedGroupCandidate(
      platform=platform,
      group_type=group_type,
      source_url=page_url,
      context=context,
      image_url=stored_path,
      image_bytes=image_bytes,
      image_content_type=content_type,
      fallback_url=fallback_url or page_url,
      decoded_payload=decoded_payload,
      qrcode_verified=bool(decoded_payload),
      source_urls=[page_url],
    )

  def _extract_context(self, tag: Tag) -> str:
    parts: list[str] = []
    block = self._nearest_context_block(tag)

    parts.extend(self._tag_text_parts(tag))

    if block is not None:
      parts.append(self._truncate_text(block.get_text(' ', strip=True)))
      heading = self._nearest_heading(block)
      if heading is not None:
        parts.append(self._truncate_text(heading.get_text(' ', strip=True)))

      for sibling in (block.previous_sibling, block.next_sibling):
        if isinstance(sibling, Tag) and sibling.name in CONTEXT_BLOCK_TAGS | HEADING_TAGS:
          parts.append(self._truncate_text(sibling.get_text(' ', strip=True)))
        elif isinstance(sibling, NavigableString):
          text = str(sibling).strip()
          if text:
            parts.append(self._truncate_text(text))

    normalized = ' '.join(filter(None, parts))
    return re.sub(r'\s+', ' ', normalized).strip()

  def _nearest_context_block(self, tag: Tag) -> Tag | None:
    current: Tag | None = tag

    while current is not None:
      if current.name in CONTEXT_BLOCK_TAGS:
        return current
      current = current.parent if isinstance(current.parent, Tag) else None

    return None

  def _nearest_heading(self, tag: Tag) -> Tag | None:
    current: Tag | None = tag

    while current is not None:
      sibling = current.previous_sibling
      while sibling is not None:
        if isinstance(sibling, Tag):
          if sibling.name in HEADING_TAGS:
            return sibling
          nested_heading = sibling.find(list(HEADING_TAGS))
          if nested_heading is not None:
            return nested_heading
        sibling = sibling.previous_sibling

      current = current.parent if isinstance(current.parent, Tag) else None

    return None

  def _tag_text_parts(self, tag: Tag) -> list[str]:
    parts = [
      tag.get_text(' ', strip=True),
      tag.get('alt', ''),
      tag.get('title', ''),
      tag.get('aria-label', ''),
      tag.get('data-canonical-src', ''),
    ]
    return [self._truncate_text(part) for part in parts if part]

  def _truncate_text(self, value: str, limit: int = 180) -> str:
    value = value.strip()
    return value[:limit]

  def _positive_context_level(self, text: str) -> int:
    if any(keyword in text for keyword in STRONG_POSITIVE_KEYWORDS):
      return 2

    if any(keyword in text for keyword in WEAK_POSITIVE_KEYWORDS):
      return 1

    return 0

  def _is_negative_context(self, text: str) -> bool:
    return any(keyword in text for keyword in NEGATIVE_KEYWORDS)

  def _detect_platform(self, *sources: str | None) -> Platform | None:
    haystack = ' '.join(filter(None, sources)).lower()

    if any(token in haystack for token in PLATFORM_HINTS[Platform.WECHAT]):
      return Platform.WECHAT

    if self._looks_like_qq_group(haystack):
      return Platform.QQ

    if any(token in haystack for token in PLATFORM_HINTS[Platform.FEISHU]):
      return Platform.FEISHU

    return None

  def _looks_like_qq_group(self, haystack: str) -> bool:
    if any(token in haystack for token in ('qm.qq.com', 'qun.qq.com', 'jq.qq.com')):
      return True

    return any(
      re.search(pattern, haystack)
      for pattern in (
        r'qq\s*官方群',
        r'qq\s*群',
        r'加入\s*qq\s*群',
        r'qq群',
        r'qq群聊',
      )
    )

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

    result = self._try_decode_image(image)
    if result[1]:
      return result

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    result = self._try_decode_image(gray)
    if result[1]:
      return result

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    result = self._try_decode_image(enhanced)
    if result[1]:
      return result

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    result = self._try_decode_image(binary)
    if result[1]:
      return result

    h, w = gray.shape[:2]
    if h < 200 or w < 200:
      scale = max(2, min(4, 300 / max(h, w)))
      upscaled = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
      result = self._try_decode_image(upscaled)
      if result[1]:
        return result

    return None, False

  def _try_decode_image(self, image) -> tuple[str | None, bool]:
    detector = cv2.QRCodeDetector()

    decoded_text, points, _ = detector.detectAndDecode(image)
    if points is not None:
      return (decoded_text or None), True

    try:
      detected, decoded_info, points_multi, _ = detector.detectAndDecodeMulti(image)
    except Exception:
      return None, False

    if detected and points_multi is not None:
      joined = ' '.join(item for item in decoded_info if item)
      return (joined or None), True

    return None, False

  def _looks_like_qrcode_image(
    self,
    image_bytes: bytes,
    image_url: str,
    detected_qrcode: bool,
  ) -> bool:
    lowered_url = image_url.lower()

    if any(token in lowered_url for token in QRCODE_URL_HINTS):
      return True

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(image_array, cv2.IMREAD_GRAYSCALE)

    if image is None:
      return False

    height, width = image.shape[:2]
    squareish = abs(height - width) / max(height, width) <= 0.2

    if not squareish:
      return False

    if detected_qrcode:
      return True

    contrast = int(image.max()) - int(image.min())
    _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    black_ratio = float(np.mean(binary == 0))
    return contrast >= 80 and 0.12 <= black_ratio <= 0.88

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

    for _ in range(4):
      if current is None:
        break

      anchor = current if current.name == 'a' else current.find('a', href=True)

      if anchor and anchor.get('href'):
        resolved = urljoin(page_url, anchor['href'])
        if not self._is_link_noise(anchor, resolved, page_url):
          return resolved

      current = current.parent if isinstance(current.parent, Tag) else None

    return None

  def _resolve_image_source(self, tag: Tag) -> str | None:
    for attribute in ('data-canonical-src', 'data-src', 'data-original', 'data-lazy-src', 'src'):
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

  def _unwrap_image_url(self, image_url: str) -> str:
    parsed = urlparse(image_url)

    if '/_next/image' in parsed.path:
      nested = parse_qs(parsed.query).get('url', [''])[0]
      if nested:
        return unquote(nested)

    if parsed.netloc.lower() == 'camo.githubusercontent.com':
      segments = [segment for segment in parsed.path.split('/') if segment]
      if segments:
        encoded = segments[-1]
        try:
          return bytes.fromhex(encoded).decode('utf-8')
        except ValueError:
          return image_url

    return image_url

  def _link_has_platform_signal(self, absolute_url: str, context: str) -> bool:
    haystack = f'{absolute_url} {context}'.lower()

    return any(
      token in haystack
      for tokens in PLATFORM_HINTS.values()
      for token in tokens
    )

  def _is_link_noise(self, tag: Tag, absolute_url: str, page_url: str) -> bool:
    href = tag.get('href', '')
    parsed_link = urlparse(absolute_url)
    parsed_page = urlparse(page_url)
    tag_text = tag.get_text(' ', strip=True).lower()
    class_names = ' '.join(tag.get('class', [])).lower()
    aria_label = (tag.get('aria-label') or '').lower()

    if href.startswith('#'):
      return True

    if parsed_link.netloc == parsed_page.netloc and parsed_link.path == parsed_page.path and parsed_link.fragment:
      return True

    if class_names == 'anchor' or class_names.endswith(' anchor'):
      return True

    if aria_label.startswith('permalink'):
      return True

    if tag_text in {'', '#', 'readme', 'top'}:
      return True

    return False

  def _deduplicate_candidates(
    self,
    candidates: list[ExtractedGroupCandidate],
    stats: ExtractionStats | None,
  ) -> list[ExtractedGroupCandidate]:
    best_by_key: dict[str, ExtractedGroupCandidate] = {}

    for candidate in sorted(candidates, key=self._candidate_priority, reverse=True):
      key = self._candidate_bucket_key(candidate)

      if key in best_by_key:
        if stats is not None:
          stats.deduplicated_candidates += 1
        continue

      best_by_key[key] = candidate

    return list(best_by_key.values())

  def _candidate_bucket_key(self, candidate: ExtractedGroupCandidate) -> str:
    normalized_entry = (candidate.entry_url or candidate.fallback_url or candidate.source_url).split('#', 1)[0]
    context_key = re.sub(r'\s+', ' ', candidate.context.lower())[:100]
    return '|'.join(
      [
        candidate.platform.value,
        candidate.group_type.value,
        candidate.source_url,
        normalized_entry,
        context_key,
      ],
    )

  def _candidate_priority(self, candidate: ExtractedGroupCandidate) -> tuple[int, int, int]:
    return (
      1 if candidate.image_url else 0,
      1 if candidate.qrcode_verified else 0,
      len(candidate.context),
    )
