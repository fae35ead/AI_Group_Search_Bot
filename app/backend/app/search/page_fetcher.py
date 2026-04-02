import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.core.config import Settings
from app.search.models import FetchedPage


class PageFetcher:
  def __init__(self, settings: Settings):
    self.settings = settings

  def fetch_page(self, url: str) -> FetchedPage | None:
    static_page = self._fetch_with_http(url)

    if static_page and static_page.text:
      return static_page

    return self._fetch_with_playwright(url)

  def collect_relevant_internal_links(self, page: FetchedPage, limit: int = 15) -> list[str]:
    soup = BeautifulSoup(page.html, 'html.parser')
    keywords = (
      'join',
      'invite',
      'community',
      'contact',
      'group',
      'wechat',
      'weixin',
      'qq',
      'feishu',
      'lark',
      'qr',
      'forum',
      '社群',
      '社区',
      '群',
      '联系',
      '加入',
      '答疑',
      '开发者',
      'support',
    )

    links: list[str] = []
    base_host = urlparse(page.final_url).netloc.lower()

    for anchor in soup.find_all('a', href=True):
      text = anchor.get_text(' ', strip=True)
      href = anchor['href'].strip()
      absolute = urljoin(page.final_url, href)
      parsed = urlparse(absolute)

      if not parsed.scheme.startswith('http'):
        continue

      if parsed.netloc.lower() != base_host:
        continue

      haystack = f'{text} {href}'.lower()

      if not any(keyword in haystack for keyword in keywords):
        continue

      if absolute not in links:
        links.append(absolute)

      if len(links) >= limit:
        break

    return links

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
