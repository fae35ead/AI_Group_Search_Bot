import base64
import re
from urllib.parse import urljoin, unquote, urlparse

import cv2
import httpx
import numpy as np
from bs4 import BeautifulSoup, Tag

from app.api.schemas import GroupType, Platform
from app.core.config import Settings
from app.search.models import ExtractedGroupCandidate, FetchedPage

PLATFORM_HINTS = {
  Platform.WECHAT: (
    'weixin',
    'wechat',
    '\u5fae\u4fe1',
    'weixin.qq.com',
  ),
  Platform.QQ: (
    'qm.qq.com',
    'qun.qq.com',
    'jq.qq.com',
    'qq group',
    '\u52a0\u5165qq\u7fa4',
    'qq\u7fa4',
  ),
  Platform.FEISHU: (
    'feishu',
    'larksuite',
    'lark',
    '\u98de\u4e66',
  ),
  Platform.DISCORD: (
    'discord',
    'discord.gg',
    'discordapp',
    'discord.com/invite',
  ),
  Platform.WECOM: (
    'work.weixin.qq.com',
    'wxwork',
    'work wechat',
    '\u4f01\u4e1a\u5fae\u4fe1',
    '\u4f01\u5fae',
  ),
  Platform.DINGTALK: (
    'dingtalk',
    'dingding',
    'qr.dingtalk.com',
    '\u9489\u9489',
  ),
}
GROUP_INTENT_HINTS = (
  'group',
  'community',
  'join',
  'invite',
  'server',
  'forum',
  'qrcode',
  'qr',
  'join group',
  'discussion group',
  'discussion',
  'join discussion',
  'group chat',
  'group discussion',
  'discord server',
  'wechat group',
  'qq group',
  'feishu group',
  'lark group',
  'work wechat',
  'wxwork',
  'dingtalk',
  'dingding',
  '\u5b98\u65b9\u7fa4',
  '\u4ea4\u6d41\u7fa4',
  '\u8ba8\u8bba\u7fa4',
  '\u5165\u7fa4',
  '\u52a0\u7fa4',
  '\u52a0\u5165\u7fa4\u804a',
  '\u793e\u7fa4',
  '\u793e\u533a',
  '\u4e8c\u7ef4\u7801',
  '\u626b\u7801',
  '\u4f01\u4e1a\u5fae\u4fe1',
  '\u9489\u9489',
)
STRONG_GROUP_INTENT_HINTS = (
  'group',
  'community',
  'official group',
  'discussion group',
  'join group',
  'join discussion',
  'group discussion',
  'group chat',
  '\u5b98\u65b9\u7fa4',
  '\u4ea4\u6d41\u7fa4',
  '\u8ba8\u8bba\u7fa4',
  '\u5165\u7fa4',
  '\u52a0\u5165\u7fa4\u804a',
  '\u793e\u7fa4',
)
ACCOUNT_INTENT_HINTS = (
  'official account',
  'wechat id',
  'scan to follow',
  'follow us',
  'contact us',
  'customer service wechat',
  'customer support wechat',
  '\u516c\u4f17\u53f7',
  '\u5fae\u4fe1\u53f7',
  '\u626b\u7801\u5173\u6ce8',
  '\u8054\u7cfb\u6211\u4eec',
  '\u5ba2\u670d\u5fae\u4fe1',
  '\u5ba2\u670d',
)
NEGATIVE_CONTEXT_HINTS = (
  'screenshot',
  '\u622a\u56fe',
  'chat history',
  'conversation',
  'message',
  'record',
  'history',
  'pricing',
  'newsletter',
  'changelog',
  'docs',
  'documentation',
  'document',
  'readme',
  'tutorial',
  'payment',
  'pay',
  'wallet',
  'invoice',
  'transfer',
  '\u804a\u5929\u8bb0\u5f55',
  '\u7fa4\u804a\u8bb0\u5f55',
  '\u6587\u6863',
  '\u8bf4\u660e',
  '\u8bf4\u660e\u6587\u6863',
  '\u6559\u7a0b',
  '\u793a\u4f8b',
  '\u811a\u672c',
  '\u914d\u7f6e',
  '\u6536\u6b3e',
  '\u4ed8\u6b3e',
  '\u652f\u4ed8',
  '\u6253\u8d4f',
)
QQ_GROUP_CONTEXT_HINTS = (
  'qq 群',
  'qq群',
  'qq群讨论',
  'qq群交流',
  'qqgroup',
  'qq group',
  'qq discussion',
  'discussion group',
  '交流群',
  '讨论群',
  '加入群',
  '加群',
  '加qq群',
  'qq群号',
  '入群',
  '进群',
)
QQ_ACCOUNT_CONTEXT_HINTS = (
  'qq号',
  'qq 号',
  '客服',
  '联系我们',
  '公众号',
  '微信号',
)
QQ_GROUP_NUMBER_PATTERN = re.compile(r'(?<!\d)([1-9]\d{4,11})(?!\d)')
QQ_CONTEXT_WINDOW_RADIUS = 1
QQ_SCAN_MAX_LINES = 160
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.webp', '.svg')
MAX_CANDIDATES_PER_PAGE = 30
MAX_VISUAL_ATTEMPTS_PER_PAGE = 12
MIN_IMAGE_SIZE = 180
MIN_SMALL_QR_IMAGE_SIZE = 160
MIN_VISUAL_SIZE = 400
MIN_VISUAL_BYTES = 20_000
MAX_VISUAL_RATIO = 1.5
SMALL_QR_MAX_RATIO = 1.35
SMALL_QR_BORDER_RETRY_MAX_SIDE = 420
SMALL_QR_BORDER_PADDINGS = (20, 40)
QR_PYRAMID_SCALES = (1.0, 0.75, 0.5)
QR_SLIDING_WINDOW_SIZE = 640
QR_SLIDING_WINDOW_STRIDE = 320
QR_SLIDING_MIN_LONG_SIDE = 1200
QR_MAX_WINDOWS_PER_SCALE = 24
QR_PREPROCESS_SCALES = (1.0, 1.5)
QR_PREPROCESS_MAX_SIDE = 900
QRCODE_URL_HINTS = (
  'qrcode',
  'qr-code',
  'qr_',
  '/qr',
  '-qr',
  '_qr',
  'scan-code',
  'invite-qr',
)
SHORTLINK_HOSTS = {
  't.co',
  'bit.ly',
  'tinyurl.com',
  's.id',
  'surl.cn',
  'u.wechat.com',
  'url.cn',
  'reurl.cc',
}
NOISE_LINK_HOSTS = {
  'amazon.com',
  'www.amazon.com',
  'taobao.com',
  'www.taobao.com',
  'jd.com',
  'www.jd.com',
  'tmall.com',
  'www.tmall.com',
  'ebay.com',
  'www.ebay.com',
}



class EntryExtractor:
  def __init__(self, settings: Settings):
    self.settings = settings
    self._client = httpx.Client(
      headers={'User-Agent': settings.user_agent},
      follow_redirects=True,
      timeout=settings.request_timeout_seconds,
    )

  def __del__(self):
    self._client.close()

  def extract(self, pages: list[FetchedPage]) -> list[ExtractedGroupCandidate]:
    candidates: list[ExtractedGroupCandidate] = []
    candidate_count = 0

    for page in pages:
      if candidate_count >= MAX_CANDIDATES_PER_PAGE:
        break

      soup = BeautifulSoup(page.html, 'html.parser')
      readme_blocks = self._find_readme_blocks(soup)
      blocks = readme_blocks or [soup]
      visual_attempts = 0

      for block in blocks:
        if candidate_count >= MAX_CANDIDATES_PER_PAGE:
          break

        images = sorted(
          block.find_all('img'),
          key=lambda image: self._score_image_priority(image, page.final_url),
          reverse=True,
        )
        for image in images:
          if candidate_count >= MAX_CANDIDATES_PER_PAGE or visual_attempts >= MAX_VISUAL_ATTEMPTS_PER_PAGE:
            break
          nearby = self._collect_nearby_text(image)
          if not self._should_try_image_tag(image, page.final_url, nearby):
            continue
          candidate = self._extract_from_img_tag(image, page.final_url, context=nearby)
          visual_attempts += 1
          if candidate:
            candidates.append(candidate)
            candidate_count += 1

        for link in block.find_all('a', href=True):
          if candidate_count >= MAX_CANDIDATES_PER_PAGE:
            break
          nearby = self._collect_nearby_text(link)
          absolute_link = urljoin(page.final_url, link.get('href', ''))
          is_image_link = self._looks_like_image_url(absolute_link)
          if is_image_link and visual_attempts >= MAX_VISUAL_ATTEMPTS_PER_PAGE:
            continue
          candidate = self._extract_from_link_tag(link, page.final_url, context=nearby)
          if candidate:
            if candidate.image_bytes is not None:
              visual_attempts += 1
            candidates.append(candidate)
            candidate_count += 1

        if candidate_count >= MAX_CANDIDATES_PER_PAGE:
          continue

        for candidate in self._extract_qq_number_candidates(block, page.final_url):
          if candidate_count >= MAX_CANDIDATES_PER_PAGE:
            break
          candidates.append(candidate)
          candidate_count += 1

    return candidates

  # -------------------------------------------------------------------------
  # README / context helpers
  # -------------------------------------------------------------------------

  def _find_readme_blocks(self, soup: BeautifulSoup) -> list[Tag]:
    blocks: list[Tag] = []
    for selector in (
      'article.markdown-body',
      'div.markdown-body',
      'article[itemprop="description"]',
      'div[data-pjax] article',
      '[class*="readme"]',
      'article',
      'main',
    ):
      for element in soup.select(selector)[:3]:
        if isinstance(element, Tag):
          blocks.append(element)
      if blocks:
        break
    return blocks

  def _collect_nearby_text(self, tag: Tag) -> str:
    parts: list[str] = []
    for attr in ('alt', 'title', 'aria-label'):
      value = tag.get(attr, '').strip()
      if value:
        parts.append(value)

    anchor = tag if tag.name == 'a' else tag.find_parent('a', href=True)
    if isinstance(anchor, Tag):
      anchor_image_signals = self._collect_anchor_image_signals(anchor)
      if anchor_image_signals:
        parts.append(anchor_image_signals)

    text = tag.get_text(' ', strip=True)
    if text:
      parts.append(text[:200])

    parent = tag.find_parent(['a', 'div', 'section', 'article', 'li', 'p'])
    if parent:
      sibling_image_count = len(parent.find_all('img')) if tag.name == 'img' else 0
      anchor_contains_image = tag.name == 'a' and tag.find('img') is not None
      # When multiple images share one parent, parent-level text often mixes contexts.
      # Skip broad parent text in this case to reduce cross-image contamination.
      if sibling_image_count <= 1 and not anchor_contains_image:
        parent_text = parent.get_text(' ', strip=True)
        if parent_text:
          parts.append(parent_text[:300])

    sibling_heading = tag.find_previous(['h1', 'h2', 'h3', 'h4', 'strong'])
    if sibling_heading:
      heading_text = sibling_heading.get_text(' ', strip=True)
      if heading_text:
        parts.append(heading_text[:120])

    return ' '.join(parts)

  def _collect_anchor_image_signals(self, anchor: Tag) -> str:
    parts: list[str] = []
    for image in anchor.find_all('img')[:4]:
      for attr in ('alt', 'title', 'aria-label'):
        value = image.get(attr, '').strip()
        if value:
          parts.append(value)
      src = self._resolve_image_source(image)
      if src:
        parts.append(src)

    if not parts:
      return ''
    deduped = list(dict.fromkeys(parts))
    return ' '.join(deduped)

  def _extract_qq_number_candidates(self, block: Tag, page_url: str) -> list[ExtractedGroupCandidate]:
    text = block.get_text('\n', strip=True)
    if not text:
      return []

    candidates: list[ExtractedGroupCandidate] = []
    seen_numbers: set[str] = set()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines[:QQ_SCAN_MAX_LINES]):
      window_start = max(0, index - QQ_CONTEXT_WINDOW_RADIUS)
      window_end = min(len(lines), index + QQ_CONTEXT_WINDOW_RADIUS + 1)
      nearby_lines = lines[window_start:window_end]
      context_text = ' '.join(nearby_lines).lower()
      if not self._has_qq_group_context(context_text):
        continue
      if self._has_qq_account_context(context_text):
        continue

      for match in QQ_GROUP_NUMBER_PATTERN.finditer(line):
        qq_number = match.group(1)
        if qq_number in seen_numbers:
          continue
        seen_numbers.add(qq_number)
        context_preview = ' | '.join(nearby_lines)[:300]
        candidates.append(
          ExtractedGroupCandidate(
            platform=Platform.QQ,
            group_type=GroupType.UNKNOWN,
            source_url=page_url,
            context=context_preview,
            image_url=None,
            image_bytes=None,
            image_content_type=None,
            entry_url=None,
            fallback_url=page_url,
            decoded_payload=f'qq:{qq_number}',
            qq_number=qq_number,
            qrcode_verified=False,
            source_urls=[page_url],
          ),
        )
        if len(candidates) >= 5:
          return candidates

    return candidates

  def _score_image_priority(self, tag: Tag, page_url: str) -> int:
    src = self._resolve_image_source(tag)
    if not src:
      return 0
    image_url = urljoin(page_url, src)
    nearby = self._collect_nearby_text(tag)
    score = 0
    lowered = f'{image_url} {nearby}'.lower()
    if self._looks_like_qrcode_url(image_url):
      score += 60
    if self._has_group_intent(lowered):
      score += 35
    if self._has_strong_group_intent(lowered):
      score += 20
    if self._has_account_intent(lowered) and not self._has_strong_group_intent(lowered):
      score -= 40
    if self._detect_platform(lowered) is not None:
      score += 25
    if self._has_negative_context(lowered):
      score -= 30
    return score

  def _should_try_image_tag(self, tag: Tag, page_url: str, context: str) -> bool:
    src = self._resolve_image_source(tag)
    if not src:
      return False
    image_url = urljoin(page_url, src)
    nearest_link = self._find_nearest_link(tag, page_url)
    full_context = f'{context} {nearest_link or ""} {image_url}'.strip()
    resolved_url = self._resolve_camo_url(image_url)
    return self._should_consider_visual_candidate(resolved_url, full_context, nearest_link)

  # -------------------------------------------------------------------------
  # Image extraction
  # -------------------------------------------------------------------------

  def _extract_from_img_tag(
    self,
    tag: Tag,
    page_url: str,
    context: str = '',
  ) -> ExtractedGroupCandidate | None:
    src = self._resolve_image_source(tag)
    if not src:
      return None

    image_url = urljoin(page_url, src)
    nearest_link = self._find_nearest_link(tag, page_url)
    full_context = f'{context} {nearest_link or ""} {image_url}'.strip()
    resolved_url = self._resolve_camo_url(image_url)

    if not self._should_consider_visual_candidate(resolved_url, full_context, nearest_link):
      return None

    content = self._download_image(image_url)
    if content is None:
      return None
    image_bytes, content_type = content

    return self._extract_visual_candidate(
      image_url=image_url,
      page_url=page_url,
      full_context=full_context,
      image_bytes=image_bytes,
      content_type=content_type,
      entry_url=nearest_link,
    )

  # -------------------------------------------------------------------------
  # Link extraction
  # -------------------------------------------------------------------------

  def _extract_from_link_tag(
    self,
    tag: Tag,
    page_url: str,
    context: str = '',
  ) -> ExtractedGroupCandidate | None:
    href = tag.get('href')
    if not href:
      return None

    absolute = urljoin(page_url, href)
    own_text = tag.get_text(' ', strip=True)
    anchor_image_signals = self._collect_anchor_image_signals(tag)
    full_context = f'{own_text} {anchor_image_signals} {context} {absolute}'.strip()
    platform = self._detect_platform(absolute, own_text, anchor_image_signals, context)
    if platform is None:
      return None

    if self._looks_like_image_url(absolute):
      resolved_url = self._resolve_camo_url(absolute)
      if not self._should_consider_visual_candidate(resolved_url, full_context, None):
        return None

      content = self._download_image(absolute)
      if content is None:
        return None
      image_bytes, content_type = content

      return self._extract_visual_candidate(
        image_url=absolute,
        page_url=page_url,
        full_context=full_context,
        image_bytes=image_bytes,
        content_type=content_type,
        entry_url=None,
      )

    resolved_group_link = self._resolve_group_link(absolute, platform)
    if resolved_group_link is None:
      return None

    return ExtractedGroupCandidate(
      platform=platform,
      group_type=GroupType.UNKNOWN,
      source_url=page_url,
      context=full_context,
      image_url=None,
      image_bytes=None,
      image_content_type=None,
      entry_url=resolved_group_link,
      fallback_url=resolved_group_link,
      decoded_payload=None,
      qrcode_verified=False,
      source_urls=[page_url],
    )

  def _extract_visual_candidate(
    self,
    *,
    image_url: str,
    page_url: str,
    full_context: str,
    image_bytes: bytes,
    content_type: str | None,
    entry_url: str | None,
  ) -> ExtractedGroupCandidate | None:
    image = self._decode_image(image_bytes)
    if image is None:
      return None

    height, width = image.shape[:2]
    platform_from_text = self._detect_platform(image_url, full_context, entry_url)
    has_group_intent = self._has_group_intent(full_context)
    has_strong_group_intent = self._has_strong_group_intent(full_context)
    has_account_intent = self._has_account_intent(full_context)
    has_negative_context = self._has_negative_context(full_context)
    has_qr_url_hint = self._looks_like_qrcode_url(image_url)
    resolved_entry_from_text = (
      self._resolve_group_link(entry_url, platform_from_text)
      if entry_url and platform_from_text
      else None
    )
    is_small_square = self._is_near_square(width, height, max_ratio=SMALL_QR_MAX_RATIO)

    if has_account_intent and not has_strong_group_intent and resolved_entry_from_text is None:
      return None

    if width < MIN_IMAGE_SIZE or height < MIN_IMAGE_SIZE:
      # Allow small near-square QR-like images when link/context signals are strong.
      has_small_qr_signal = (
        min(width, height) >= MIN_SMALL_QR_IMAGE_SIZE
        and is_small_square
        and not has_negative_context
        and platform_from_text is not None
        and has_group_intent
        and (has_qr_url_hint or resolved_entry_from_text is not None)
      )
      if not has_small_qr_signal:
        return None

    decoded_payload, qr_points = self._analyze_qrcode(image)
    qr_detected = qr_points is not None
    cropped_bytes = self._crop_qrcode(image, qr_points) if qr_detected else None
    final_image_bytes = cropped_bytes or image_bytes
    final_content_type = 'image/png' if cropped_bytes else content_type

    if not qr_detected and self._is_visually_noisy(width, height, len(image_bytes)):
      return None

    platform_from_decode = self._detect_platform(decoded_payload) if decoded_payload else None
    if decoded_payload and platform_from_decode:
      resolved_entry_url = (
        self._resolve_group_link(entry_url, platform_from_decode)
        if entry_url
        else None
      )
      return self._build_visual_candidate(
        platform=platform_from_decode,
        page_url=page_url,
        full_context=full_context,
        image_url=image_url,
        image_bytes=final_image_bytes,
        content_type=final_content_type,
        entry_url=resolved_entry_url,
        decoded_payload=decoded_payload,
        verified=True,
      )

    # If we only detected qr points but cannot decode or crop a qr area,
    # treat it as weak signal and drop to avoid screenshot false positives.
    if qr_detected and decoded_payload is None and cropped_bytes is None:
      return None

    if qr_detected and platform_from_text and has_group_intent:
      if not resolved_entry_from_text and not has_qr_url_hint and not has_strong_group_intent:
        return None
      if has_account_intent and not has_strong_group_intent and not resolved_entry_from_text:
        return None
      if has_negative_context and not has_qr_url_hint and not resolved_entry_from_text:
        return None
      has_large_qr_link_signal = has_qr_url_hint and min(width, height) >= MIN_IMAGE_SIZE
      high_confidence_undecoded = (
        decoded_payload is None
        and cropped_bytes is not None
        and not has_negative_context
        and not has_account_intent
        and self._is_near_square(width, height, max_ratio=SMALL_QR_MAX_RATIO)
        and (
          has_strong_group_intent
          or resolved_entry_from_text is not None
          or has_large_qr_link_signal
        )
      )
      return self._build_visual_candidate(
        platform=platform_from_text,
        page_url=page_url,
        full_context=full_context,
        image_url=image_url,
        image_bytes=final_image_bytes,
        content_type=final_content_type,
        entry_url=resolved_entry_from_text,
        decoded_payload=decoded_payload,
        verified=high_confidence_undecoded,
      )

    if platform_from_text and has_group_intent and self._passes_visual_size(width, height, len(image_bytes)):
      if has_negative_context:
        return None
      if not resolved_entry_from_text and not has_qr_url_hint and not has_strong_group_intent:
        return None
      return self._build_visual_candidate(
        platform=platform_from_text,
        page_url=page_url,
        full_context=full_context,
        image_url=image_url,
        image_bytes=image_bytes,
        content_type=content_type,
        entry_url=resolved_entry_from_text,
        decoded_payload=None,
        verified=False,
      )

    return None

  def _build_visual_candidate(
    self,
    *,
    platform: Platform,
    page_url: str,
    full_context: str,
    image_url: str,
    image_bytes: bytes,
    content_type: str | None,
    entry_url: str | None,
    decoded_payload: str | None,
    verified: bool,
  ) -> ExtractedGroupCandidate:
    return ExtractedGroupCandidate(
      platform=platform,
      group_type=GroupType.UNKNOWN,
      source_url=page_url,
      context=full_context,
      image_url=image_url,
      image_bytes=image_bytes,
      image_content_type=content_type,
      entry_url=entry_url,
      fallback_url=entry_url or page_url,
      decoded_payload=decoded_payload,
      qrcode_verified=verified,
      source_urls=[page_url],
    )

  # -------------------------------------------------------------------------
  # Candidate filtering
  # -------------------------------------------------------------------------

  def _should_consider_visual_candidate(
    self,
    image_url: str,
    context: str,
    entry_url: str | None,
  ) -> bool:
    signal_text = ' '.join(filter(None, [image_url, context, entry_url])).lower()
    has_platform = self._detect_platform(signal_text) is not None
    has_group_intent = self._has_group_intent(signal_text)
    has_account_intent = self._has_account_intent(signal_text)
    has_qr_url_hint = self._looks_like_qrcode_url(image_url)
    has_negative_only = self._has_negative_context(signal_text) and not has_group_intent
    if has_negative_only and not has_qr_url_hint:
      return False
    if has_account_intent and not has_group_intent and not has_qr_url_hint:
      return False
    return has_platform or has_group_intent or has_qr_url_hint

  def _has_group_intent(self, text: str) -> bool:
    return self._group_intent_score(text) > 0

  def _has_strong_group_intent(self, text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in STRONG_GROUP_INTENT_HINTS)

  def _group_intent_score(self, text: str) -> int:
    lowered = text.lower()
    score = 0
    for token in GROUP_INTENT_HINTS:
      if token in lowered:
        score += 1
    for token in STRONG_GROUP_INTENT_HINTS:
      if token in lowered:
        score += 2
    return score

  def _has_account_intent(self, text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ACCOUNT_INTENT_HINTS)

  def _has_negative_context(self, text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in NEGATIVE_CONTEXT_HINTS)

  def _has_qq_group_context(self, text: str) -> bool:
    lowered = text.lower()
    if 'qq' not in lowered and '群' not in lowered:
      return False
    return any(token in lowered for token in QQ_GROUP_CONTEXT_HINTS)

  def _has_qq_account_context(self, text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in QQ_ACCOUNT_CONTEXT_HINTS)

  def _passes_visual_size(self, width: int, height: int, total_bytes: int) -> bool:
    return width >= MIN_VISUAL_SIZE and height >= MIN_VISUAL_SIZE and total_bytes >= MIN_VISUAL_BYTES

  def _is_visually_noisy(self, width: int, height: int, total_bytes: int) -> bool:
    ratio = max(width, height) / max(1, min(width, height))
    if ratio > MAX_VISUAL_RATIO:
      return True
    return not self._passes_visual_size(width, height, total_bytes)

  def _is_near_square(self, width: int, height: int, *, max_ratio: float = MAX_VISUAL_RATIO) -> bool:
    ratio = max(width, height) / max(1, min(width, height))
    return ratio <= max_ratio

  def _looks_like_image_url(self, url: str) -> bool:
    lowered = url.lower()
    parsed = urlparse(lowered)
    path = parsed.path or lowered
    return any(path.endswith(ext) for ext in IMAGE_EXTENSIONS) or 'image' in lowered

  def _looks_like_qrcode_url(self, url: str) -> bool:
    lowered = url.lower()
    return any(token in lowered for token in QRCODE_URL_HINTS)

  def _decode_image(self, image_bytes: bytes) -> np.ndarray | None:
    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    return cv2.imdecode(image_array, cv2.IMREAD_COLOR)

  # -------------------------------------------------------------------------
  # Download / QR analysis
  # -------------------------------------------------------------------------

  def _download_image(self, image_url: str) -> tuple[bytes, str] | None:
    resolved_url = self._resolve_camo_url(image_url)
    if resolved_url.startswith('data:image/'):
      header, encoded = resolved_url.split(',', 1)
      mime = header.split(';', 1)[0].split(':', 1)[1]
      try:
        data = base64.b64decode(encoded)
        if len(data) > 5 * 1024 * 1024:
          return None
        return data, mime
      except Exception:
        return None

    try:
      response = self._client.get(resolved_url)
      response.raise_for_status()
      if len(response.content) > 5 * 1024 * 1024:
        return None
    except httpx.HTTPError:
      return None

    return response.content, response.headers.get('content-type', 'image/png')

  def _resolve_camo_url(self, url: str) -> str:
    if not url.startswith('https://camo.githubusercontent.com/'):
      return url

    parts = url.split('/')
    if len(parts) < 5:
      return url

    hex_part = parts[-1]
    try:
      real_url = bytes.fromhex(hex_part).decode('utf-8')
      if real_url.startswith('http'):
        return real_url
    except (ValueError, UnicodeDecodeError):
      pass

    parsed = urlparse(url)
    query = dict([part.split('=', 1) for part in parsed.query.split('&') if '=' in part])
    origin = query.get('url')
    return unquote(origin) if origin else url

  def _analyze_qrcode(self, image: np.ndarray) -> tuple[str | None, np.ndarray | None]:
    detector = cv2.QRCodeDetector()
    payload, points = self._detect_qrcode_once(detector, image)
    if points is not None:
      return payload, points

    height, width = image.shape[:2]
    if max(height, width) <= QR_PREPROCESS_MAX_SIDE:
      preprocessed_payload, preprocessed_points = self._detect_qrcode_with_preprocess(detector, image)
      if preprocessed_points is not None:
        return preprocessed_payload, preprocessed_points

    if max(height, width) <= SMALL_QR_BORDER_RETRY_MAX_SIDE:
      bordered_payload, bordered_points = self._detect_qrcode_with_white_border(detector, image)
      if bordered_points is not None:
        return bordered_payload, bordered_points

    if max(height, width) < QR_SLIDING_MIN_LONG_SIDE:
      return None, None

    for scale in QR_PYRAMID_SCALES:
      if scale == 1.0:
        scaled = image
      else:
        scaled = cv2.resize(
          image,
          (max(1, int(width * scale)), max(1, int(height * scale))),
          interpolation=cv2.INTER_AREA,
        )

      for x1, y1, x2, y2 in self._iter_sliding_windows(
        scaled.shape[1],
        scaled.shape[0],
        window_size=QR_SLIDING_WINDOW_SIZE,
        stride=QR_SLIDING_WINDOW_STRIDE,
        max_windows=QR_MAX_WINDOWS_PER_SCALE,
      ):
        patch = scaled[y1:y2, x1:x2]
        patch_payload, patch_points = self._detect_qrcode_once(detector, patch)
        if patch_points is None:
          continue

        mapped = self._map_patch_points_to_original(patch_points, x1, y1, scale)
        if mapped is not None:
          return patch_payload, mapped

    return None, None

  def _detect_qrcode_with_preprocess(
    self,
    detector: cv2.QRCodeDetector,
    image: np.ndarray,
  ) -> tuple[str | None, np.ndarray | None]:
    grayscale = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    base_height, base_width = grayscale.shape[:2]

    for scale in QR_PREPROCESS_SCALES:
      if scale == 1.0:
        processed = grayscale
      else:
        processed = cv2.resize(
          grayscale,
          (max(1, int(base_width * scale)), max(1, int(base_height * scale))),
          interpolation=cv2.INTER_CUBIC,
        )

      _, binary = cv2.threshold(processed, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
      payload, points = self._detect_qrcode_once(detector, binary)
      if points is None:
        continue

      if scale == 1.0:
        return payload, points

      mapped = self._map_patch_points_to_original(points, 0, 0, scale)
      if mapped is not None:
        return payload, mapped

    return None, None

  def _detect_qrcode_once(
    self,
    detector: cv2.QRCodeDetector,
    image: np.ndarray,
  ) -> tuple[str | None, np.ndarray | None]:
    if self.settings.enable_opencv_qr_decode:
      decoded_text, points, _ = detector.detectAndDecode(image)
      payload = decoded_text.strip() if decoded_text else None
      if points is not None and len(points) > 0:
        return payload, points
      return None, None

    detected, points = detector.detect(image)
    if not detected or points is None or len(points) == 0:
      return None, None
    return None, points

  def _detect_qrcode_with_white_border(
    self,
    detector: cv2.QRCodeDetector,
    image: np.ndarray,
  ) -> tuple[str | None, np.ndarray | None]:
    height, width = image.shape[:2]
    for padding in SMALL_QR_BORDER_PADDINGS:
      bordered = cv2.copyMakeBorder(
        image,
        padding,
        padding,
        padding,
        padding,
        borderType=cv2.BORDER_CONSTANT,
        value=(255, 255, 255),
      )
      payload, points = self._detect_qrcode_once(detector, bordered)
      if points is None:
        continue

      normalized = np.array(points, dtype=np.float32)
      if normalized.ndim == 3:
        normalized = normalized[0]
      if normalized.ndim != 2 or normalized.shape[0] < 4 or normalized.shape[1] != 2:
        continue

      normalized[:, 0] = np.clip(normalized[:, 0] - float(padding), 0, max(0, width - 1))
      normalized[:, 1] = np.clip(normalized[:, 1] - float(padding), 0, max(0, height - 1))
      return payload, normalized.reshape(1, normalized.shape[0], 2)

    return None, None

  def _iter_sliding_windows(
    self,
    width: int,
    height: int,
    *,
    window_size: int,
    stride: int,
    max_windows: int,
  ) -> list[tuple[int, int, int, int]]:
    if width <= 0 or height <= 0:
      return []

    if width <= window_size and height <= window_size:
      return [(0, 0, width, height)]

    x_max = max(0, width - window_size)
    y_max = max(0, height - window_size)
    xs = list(range(0, x_max + 1, stride))
    ys = list(range(0, y_max + 1, stride))
    if not xs or xs[-1] != x_max:
      xs.append(x_max)
    if not ys or ys[-1] != y_max:
      ys.append(y_max)

    windows: list[tuple[int, int, int, int]] = []
    for y1 in ys:
      for x1 in xs:
        x2 = min(x1 + window_size, width)
        y2 = min(y1 + window_size, height)
        windows.append((x1, y1, x2, y2))
        if len(windows) >= max_windows:
          return windows
    return windows

  def _map_patch_points_to_original(
    self,
    points: np.ndarray,
    offset_x: int,
    offset_y: int,
    scale: float,
  ) -> np.ndarray | None:
    normalized = np.array(points, dtype=np.float32)
    if normalized.ndim == 3:
      normalized = normalized[0]
    if normalized.ndim != 2 or normalized.shape[0] < 4 or normalized.shape[1] != 2:
      return None

    normalized[:, 0] += float(offset_x)
    normalized[:, 1] += float(offset_y)
    if scale > 0:
      normalized /= float(scale)

    return normalized.reshape(1, normalized.shape[0], 2)

  def _crop_qrcode(self, image: np.ndarray, points: np.ndarray | None) -> bytes | None:
    if points is None or len(points) == 0:
      return None

    normalized = np.squeeze(points)
    if normalized.ndim != 2 or normalized.shape[0] < 4:
      return None

    xs = normalized[:, 0]
    ys = normalized[:, 1]
    padding = 12
    x1 = max(int(xs.min()) - padding, 0)
    y1 = max(int(ys.min()) - padding, 0)
    x2 = min(int(xs.max()) + padding, image.shape[1])
    y2 = min(int(ys.max()) + padding, image.shape[0])
    if x2 <= x1 or y2 <= y1:
      return None

    cropped = image[y1:y2, x1:x2]
    if cropped.size == 0:
      return None

    success, encoded = cv2.imencode('.png', cropped)
    if not success:
      return None
    return encoded.tobytes()

  # -------------------------------------------------------------------------
  # Platform / source helpers
  # -------------------------------------------------------------------------

  def _detect_platform(self, *sources: str | None) -> Platform | None:
    haystack = ' '.join(filter(None, sources)).lower()

    for token in PLATFORM_HINTS[Platform.DISCORD]:
      if token in haystack:
        return Platform.DISCORD
    for token in PLATFORM_HINTS[Platform.DINGTALK]:
      if token in haystack:
        return Platform.DINGTALK
    for token in PLATFORM_HINTS[Platform.WECOM]:
      if token in haystack:
        return Platform.WECOM
    for token in PLATFORM_HINTS[Platform.FEISHU]:
      if token in haystack:
        return Platform.FEISHU
    if self._detect_qq_platform(haystack):
      return Platform.QQ
    for token in PLATFORM_HINTS[Platform.WECHAT]:
      if token in haystack:
        return Platform.WECHAT

    return None

  def _detect_qq_platform(self, haystack: str) -> bool:
    explicit_tokens = (
      'qm.qq.com',
      'qun.qq.com',
      'jq.qq.com',
      'qq group',
      '\u52a0\u5165qq\u7fa4',
      'qq\u7fa4',
    )
    if any(token in haystack for token in explicit_tokens):
      return True
    return 'qq' in haystack and self._has_group_intent(haystack)

  def _resolve_group_link(self, url: str, platform: Platform) -> str | None:
    candidate = url.strip()
    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
      return None

    if self._is_short_link_host(parsed.netloc):
      redirected = self._follow_redirect(candidate)
      if redirected:
        candidate = redirected

    if self._is_known_non_group_link(candidate):
      return None
    if not self._is_direct_group_link(candidate, platform):
      return None
    return candidate

  def _is_short_link_host(self, host: str) -> bool:
    normalized = host.lower().removeprefix('www.')
    return normalized in SHORTLINK_HOSTS

  def _follow_redirect(self, url: str) -> str | None:
    try:
      response = self._client.get(url, follow_redirects=True)
      response.raise_for_status()
      resolved = str(response.url)
      if resolved.startswith(('http://', 'https://')):
        return resolved
    except httpx.HTTPError:
      return None
    return None

  def _is_direct_group_link(self, url: str, platform: Platform) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix('www.')
    path = parsed.path.lower()
    query = parsed.query.lower()

    if platform == Platform.DISCORD:
      return host.endswith('discord.gg') or (host.endswith('discord.com') and path.startswith('/invite/'))
    if platform == Platform.QQ:
      return host in {'qm.qq.com', 'qun.qq.com', 'jq.qq.com'}
    if platform == Platform.WECOM:
      return host.endswith('work.weixin.qq.com') and ('/gm/' in path or 'join' in path or 'invite' in path)
    if platform == Platform.DINGTALK:
      return host.endswith('qr.dingtalk.com')
    if platform == Platform.FEISHU:
      if '/share/base/form/' in path:
        return False
      return (
        ('feishu' in host or 'larksuite' in host)
        and any(token in f'{path}?{query}' for token in ('group', 'invite', 'join'))
      )
    if platform == Platform.WECHAT:
      return 'weixin.qq.com' in host and any(token in f'{path}?{query}' for token in ('group', 'join', 'invite'))

    return False

  def _is_known_non_group_link(self, url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix('www.')
    path = parsed.path.lower()
    query = parsed.query.lower()
    combined = f'{path}?{query}'

    if host in {h.removeprefix('www.') for h in NOISE_LINK_HOSTS}:
      return True

    if any(path.endswith(ext) for ext in ('.exe', '.msi', '.dmg', '.zip', '.tar.gz', '.pdf')):
      return True

    if host in {
      'mp.weixin.qq.com',
      'docs.qq.com',
      'pay.weixin.qq.com',
      'wx.tenpay.com',
      'qr.alipay.com',
      'qrpay.qq.com',
    }:
      return True

    noisy_path_tokens = (
      '/download',
      '/downloads',
      '/docs',
      '/doc/',
      '/blog',
      '/news',
      '/article',
      '/articles',
      '/workflow',
      '/workflows',
      '/integrations/',
      '/pay',
      '/payment',
      '/wallet',
      '/invoice',
      '/transfer',
    )
    if any(token in combined for token in noisy_path_tokens):
      return True

    return False

  def _resolve_image_source(self, tag: Tag) -> str | None:
    for attribute in (
      'data-canonical-src',
      'data-src',
      'data-original',
      'data-lazy-src',
      'data-original-src',
      'src',
    ):
      value = tag.get(attribute)
      if value:
        return value

    srcset = tag.get('data-srcset') or tag.get('srcset')
    if not srcset:
      return None

    entries = [entry.strip() for entry in srcset.split(',') if entry.strip()]
    for entry in reversed(entries):
      parts = entry.split()
      if parts and parts[0]:
        return parts[0]
    return None

  def _find_nearest_link(self, tag: Tag, page_url: str) -> str | None:
    current: Tag | None = tag
    for _ in range(4):
      if current is None:
        break
      anchor = current if current.name == 'a' else current.find_parent('a', href=True)
      if anchor and anchor.get('href'):
        return urljoin(page_url, anchor['href'])
      current = current.parent if isinstance(current.parent, Tag) else None
    return None
