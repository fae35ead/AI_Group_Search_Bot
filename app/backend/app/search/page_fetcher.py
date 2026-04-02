import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.core.config import Settings
from app.search.models import CandidatePageSummary, FetchedPage


class PageFetcher:
  def __init__(self, settings: Settings):
    self.settings = settings

  def fetch_page(self, url: str) -> FetchedPage | None:
    static_page = self._fetch_with_http(url)

    if static_page and static_page.text:
      return static_page

    return self._fetch_with_playwright(url)

  def collect_relevant_internal_links(self, page: FetchedPage, limit: int = 15) -> list[str]:
    return [
      candidate.url
      for candidate in self.discover_candidate_internal_links(page, limit=min(limit, 8))
    ]

  def discover_candidate_internal_links(
    self,
    page: FetchedPage,
    limit: int = 8,
  ) -> list[CandidatePageSummary]:
    soup = BeautifulSoup(page.html, 'html.parser')
    base_url = page.final_url
    base_host = urlparse(base_url).netloc.lower()
    current_path = urlparse(base_url).path.rstrip('/')
    strong_keywords = (
      'community',
      'group',
      'join',
      'invite',
      'wechat',
      'weixin',
      'qq',
      'feishu',
      'lark',
      'qr',
      'qrcode',
      'forum',
      'support',
      '社群',
      '社区',
      '群',
      '微信',
      '飞书',
      '二维码',
      '扫码',
      '加群',
      '入群',
      '答疑',
      '开发者',
    )
    weak_keywords = (
      'news',
      'blog',
      'event',
      'events',
      'developer',
      'developers',
      'ecosystem',
      'open',
      'activity',
      'activities',
      '活动',
      '新闻',
      '生态',
      '开源',
    )
    source_markers = {
      'nav': 12,
      'menu': 8,
      'footer': 12,
      'article': 10,
      'news': 10,
      'blog': 8,
      'event': 8,
      'community': 14,
      'support': 12,
      'developer': 8,
    }
    candidates_by_url: dict[str, CandidatePageSummary] = {}

    for anchor in soup.find_all('a', href=True):
      href = anchor['href'].strip()

      if not href or href.startswith(('mailto:', 'tel:', 'javascript:')):
        continue

      absolute = urljoin(base_url, href)
      parsed = urlparse(absolute)

      if not parsed.scheme.startswith('http'):
        continue

      if parsed.netloc.lower() != base_host:
        continue

      normalized_path = parsed.path.rstrip('/')

      if normalized_path == current_path and parsed.fragment:
        continue

      source_text = self._anchor_source_text(anchor, href).lower()
      score = 0
      reasons: list[str] = []

      for keyword in strong_keywords:
        if keyword in source_text:
          score += 65
          reasons.append(f'strong:{keyword}')

      for keyword in weak_keywords:
        if keyword in source_text:
          score += 25
          reasons.append(f'weak:{keyword}')

      source_bonus = self._source_marker_bonus(source_text, source_markers)
      if source_bonus:
        score += source_bonus
        reasons.append('structured-source')

      if parsed.path and any(
        token in parsed.path.lower()
        for token in ('news', 'blog', 'event', 'community', 'support', 'developer')
      ):
        score += 12
        reasons.append('path-signal')

      if anchor.get_text(' ', strip=True):
        score += min(len(anchor.get_text(' ', strip=True)), 20) // 4

      if score <= 0:
        continue

      candidate = CandidatePageSummary(
        url=absolute,
        score=score,
        source_page=page.final_url,
        source_type='internal_link',
        reasons=reasons,
      )
      existing = candidates_by_url.get(absolute)

      if existing is None or candidate.score > existing.score:
        candidates_by_url[absolute] = candidate

    candidates = sorted(
      candidates_by_url.values(),
      key=lambda item: (
        item.score,
        -self._path_depth(item.url),
        -len(item.url),
      ),
      reverse=True,
    )
    return candidates[:limit]

  def _fetch_with_http(self, url: str) -> FetchedPage | None:
    try:
      with httpx.Client(
        headers={'User-Agent': self.settings.user_agent},
        follow_redirects=True,
        timeout=self.settings.request_timeout_seconds,
      ) as client:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
      return None

    if 'text/html' not in response.headers.get('content-type', ''):
      return None

    return self._build_page(url, str(response.url), response.text, 'http')

  def _fetch_with_playwright(self, url: str) -> FetchedPage | None:
    try:
      with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(user_agent=self.settings.user_agent)
        page.goto(
          url,
          wait_until='domcontentloaded',
          timeout=int(self.settings.request_timeout_seconds * 1000),
        )
        page.wait_for_timeout(1000)

        try:
          page.wait_for_load_state('networkidle', timeout=1500)
        except Exception:
          pass

        try:
          page.evaluate(
            "() => { "
            "  document.querySelectorAll('details').forEach("
            "    d => { if (!d.hasAttribute('open')) d.setAttribute('open', ''); }"
            "  ); "
            "}",
          )
          page.wait_for_timeout(500)
        except Exception:
          pass

        try:
          page.evaluate(
            "() => { "
            "  const attrs = ['data-src', 'data-original', 'data-canonical-src']; "
            "  const imgs = Array.from(document.querySelectorAll('img')); "
            "  imgs.forEach(img => { "
            "    if (img.src && img.src !== window.location.href) return; "
            "    for (const attr of attrs) { "
            "      const value = img.getAttribute(attr); "
            "      if (value) { img.src = value; break; } "
            "    } "
            "  }); "
            "}",
          )
          page.wait_for_timeout(300)
        except Exception:
          pass

        html = page.content()
        final_url = page.url
        browser.close()
    except Exception:
      return None

    return self._build_page(url, final_url, html, 'playwright')

  def _build_page(
    self,
    requested_url: str,
    final_url: str,
    html: str,
    fetch_method: str,
  ) -> FetchedPage:
    soup = BeautifulSoup(html, 'html.parser')

    for element in soup(['script', 'style', 'noscript']):
      element.decompose()

    title = soup.title.get_text(' ', strip=True) if soup.title else final_url
    text = re.sub(r'\s+', ' ', soup.get_text(' ', strip=True)).strip()

    return FetchedPage(
      requested_url=requested_url,
      final_url=final_url,
      html=str(soup),
      title=title,
      text=text,
      fetch_method=fetch_method,
    )

  def _anchor_source_text(self, anchor, href: str) -> str:
    parts = [
      anchor.get_text(' ', strip=True),
      href,
      anchor.get('title', ''),
      anchor.get('aria-label', ''),
      ' '.join(anchor.get('class', [])),
    ]
    current = anchor.parent

    for _ in range(3):
      if current is None:
        break

      parts.append(getattr(current, 'name', '') or '')
      if hasattr(current, 'get'):
        parts.append(current.get('id', ''))
        parts.append(' '.join(current.get('class', [])))
      current = current.parent

    return ' '.join(filter(None, parts))

  def _source_marker_bonus(self, haystack: str, markers: dict[str, int]) -> int:
    bonus = 0

    for marker, score in markers.items():
      if marker in haystack:
        bonus = max(bonus, score)

    return bonus

  def _path_depth(self, url: str) -> int:
    parsed = urlparse(url)
    return len([segment for segment in parsed.path.split('/') if segment])
