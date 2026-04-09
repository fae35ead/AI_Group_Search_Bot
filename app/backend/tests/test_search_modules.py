import csv
from dataclasses import replace
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient
from bs4 import BeautifulSoup

from app.api.schemas import GroupDiscoveryStatus, GroupType, Platform, ProductCard, QQNumberEntry, RecommendationsResponse, RecommendedTool
from app.core.config import get_settings
from app.db.database import get_connection, initialize_database
from app.search.entry_extractor import EntryExtractor
from app.search.models import ExtractedGroupCandidate, FetchedPage, GitHubRepositoryCandidate
from app.search.service import SearchService
from main import app


def make_image_bytes(width: int, height: int) -> bytes:
  image = np.full((height, width, 3), 255, dtype=np.uint8)
  for i in range(0, width, 20):
    cv2.line(image, (i, 0), (i, height - 1), ((i * 13) % 255, 30, 90), 2)
  for j in range(0, height, 20):
    cv2.line(image, (0, j), (width - 1, j), (40, (j * 9) % 255, 160), 2)
  success, encoded = cv2.imencode('.png', image)
  if not success:
    raise RuntimeError('Failed to encode test image.')
  return encoded.tobytes()


class SearchServiceTests(unittest.TestCase):
  def setUp(self):
    base_settings = get_settings()
    temp_root = Path(mkdtemp())
    temp_data_dir = temp_root / 'data'
    temp_public_dir = temp_data_dir / 'public'
    temp_qrcode_dir = temp_public_dir / 'qrcodes'
    temp_viewed_dir = temp_data_dir / 'viewed'
    temp_viewed_qrcode_dir = temp_viewed_dir / 'qrcodes'
    temp_viewed_links_csv_path = temp_viewed_dir / 'viewed_links.csv'
    temp_database_path = temp_data_dir / 'ai-group-discovery.sqlite3'
    settings = replace(
      base_settings,
      data_dir=temp_data_dir,
      public_dir=temp_public_dir,
      qrcode_dir=temp_qrcode_dir,
      viewed_dir=temp_viewed_dir,
      viewed_qrcode_dir=temp_viewed_qrcode_dir,
      viewed_links_csv_path=temp_viewed_links_csv_path,
      database_path=temp_database_path,
    )
    initialize_database(settings.database_path)
    settings.public_dir.mkdir(parents=True, exist_ok=True)
    settings.qrcode_dir.mkdir(parents=True, exist_ok=True)
    settings.viewed_dir.mkdir(parents=True, exist_ok=True)
    settings.viewed_qrcode_dir.mkdir(parents=True, exist_ok=True)
    with get_connection(settings.database_path) as connection:
      connection.execute('DELETE FROM search_cache')
      connection.execute('DELETE FROM viewed_groups')
      connection.execute('DELETE FROM recommendation_pool')
      connection.execute('DELETE FROM manual_uploads')
      connection.commit()
    for export_file in settings.viewed_qrcode_dir.iterdir():
      if export_file.is_file():
        export_file.unlink()
    if settings.viewed_links_csv_path.exists():
      settings.viewed_links_csv_path.unlink()
    self.service = SearchService(settings)

  def tearDown(self):
    self.service._page_client.close()

  def _read_viewed_links_csv(self) -> list[dict[str, str]]:
    csv_path = self.service.settings.viewed_links_csv_path
    with csv_path.open('r', encoding='utf-8-sig', newline='') as csv_file:
      return list(csv.DictReader(csv_file))

  def _make_card(self, index: int) -> ProductCard:
    return ProductCard(
      product_id=f'cached-{index:03d}',
      app_name=f'Cached Tool {index}',
      description='cached',
      github_stars=1,
      created_at=None,
      verified_at=datetime.now(timezone.utc),
      groups=[],
      group_discovery_status=GroupDiscoveryStatus.NOT_FOUND,
      official_site_url=None,
      github_repo_url=None,
    )

  def test_search_returns_empty_for_blank_query(self):
    self.assertEqual(self.service.search(''), [])

  def test_search_uses_cached_results_when_fresh(self):
    cached_cards = [self._make_card(index) for index in range(3)]
    with patch.object(self.service, '_load_cached_search', return_value=cached_cards) as load_cache_mock, patch.object(
      self.service,
      '_github_search',
    ) as github_search_mock:
      results = self.service.search('cached query', limit=3)

    self.assertEqual(len(results), 3)
    load_cache_mock.assert_called_once()
    github_search_mock.assert_not_called()

  def test_search_refresh_bypasses_cache(self):
    with patch.object(self.service, '_load_cached_search') as load_cache_mock, patch.object(
      self.service,
      '_github_search',
      return_value=[],
    ), patch.object(
      self.service,
      '_build_crawl_candidates',
      return_value=[],
    ), patch.object(
      self.service,
      '_build_web_fallback_candidates',
      return_value=[],
    ), patch.object(
      self.service,
      '_save_cached_search',
    ) as save_cache_mock:
      results = self.service.search('refresh query', refresh=True)

    self.assertEqual(results, [])
    load_cache_mock.assert_not_called()
    save_cache_mock.assert_not_called()

  def test_search_cache_key_ignores_limit_changes(self):
    key_10 = self.service._build_search_cache_key('n8n', None)
    key_20 = self.service._build_search_cache_key('n8n', None)
    self.assertEqual(key_10, key_20)

  def test_search_limit_change_incrementally_fills_cache(self):
    cached_cards = [self._make_card(index) for index in range(20)]
    fresh_cards = [self._make_card(index + 20) for index in range(30)]
    fallback_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/fallback',
      full_name='example/fallback',
      repo_name='fallback',
      owner_name='example',
      owner_type='Organization',
      homepage=None,
      description='fallback',
      stars=10,
    )

    with patch.object(self.service, '_load_cached_search', return_value=cached_cards), patch.object(
      self.service,
      '_github_search',
      return_value=[],
    ), patch.object(
      self.service,
      '_build_crawl_candidates',
      return_value=[],
    ), patch.object(
      self.service,
      '_build_web_fallback_candidates',
      return_value=[fallback_candidate],
    ), patch.object(
      self.service,
      '_collect_cards',
      return_value=fresh_cards,
    ) as collect_cards_mock, patch.object(
      self.service,
      '_save_cached_search',
    ) as save_cache_mock:
      results = self.service.search('cache test', limit=50)

    self.assertEqual(len(results), 50)
    collect_cards_mock.assert_called_once()
    self.assertEqual(collect_cards_mock.call_args.kwargs.get('max_cards'), 30)
    self.assertEqual(len(collect_cards_mock.call_args.kwargs.get('exclude_product_ids') or set()), 20)
    save_cache_mock.assert_called_once()
    saved_cards = save_cache_mock.call_args.args[1]
    self.assertEqual(len(saved_cards), 50)

  def test_load_cached_search_ignores_empty_payload(self):
    cache_key = self.service._build_search_cache_key('fastgpt', None)
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(self.service.settings.database_path) as connection:
      connection.execute(
        '''
        INSERT INTO search_cache (query_key, response_json, updated_at)
        VALUES (?, ?, ?)
        ''',
        (cache_key, '[]', now),
      )
      connection.commit()

    cached = self.service._load_cached_search(cache_key)
    self.assertIsNone(cached)

  def test_normalize_legacy_qrcode_path_keeps_non_legacy_filename(self):
    path = '/assets/qrcodes/FastGPT_微信_1234abcd.png'
    normalized = self.service._normalize_legacy_qrcode_path(
      path,
      app_name='FastGPT',
      platform=Platform.WECHAT,
    )
    self.assertEqual(normalized, path)

  def test_normalize_legacy_qrcode_path_renames_legacy_file(self):
    legacy_stem = hashlib.sha1(f'legacy-rename-{time.time_ns()}'.encode('utf-8')).hexdigest()
    legacy_filename = f'{legacy_stem}.png'
    legacy_path = self.service.settings.qrcode_dir / legacy_filename
    legacy_path.write_bytes(b'legacy-bytes')
    normalized = ''
    target_path = None
    try:
      normalized = self.service._normalize_legacy_qrcode_path(
        f'/assets/qrcodes/{legacy_filename}',
        app_name='DeepSeek-V2',
        platform=Platform.WECHAT,
      )
      self.assertTrue(normalized.startswith('/assets/qrcodes/DeepSeek-V2_'))
      target_filename = normalized.rsplit('/', 1)[-1]
      target_path = self.service.settings.qrcode_dir / target_filename
      self.assertTrue(target_path.exists())
      self.assertFalse(legacy_path.exists())
    finally:
      if legacy_path.exists():
        legacy_path.unlink()
      if target_path is not None and target_path.exists():
        target_path.unlink()

  def test_normalize_legacy_qrcode_path_reuses_existing_target(self):
    legacy_stem = hashlib.sha1(f'legacy-reuse-{time.time_ns()}'.encode('utf-8')).hexdigest()
    legacy_filename = f'{legacy_stem}.jpg'
    legacy_path = self.service.settings.qrcode_dir / legacy_filename
    legacy_path.write_bytes(b'legacy-source')
    expected_filename = (
      f'DeepSeek-V2_{self.service._safe_qrcode_name(Platform.WECHAT.value, fallback="platform")}_{legacy_stem[:8]}.jpg'
    )
    target_path = self.service.settings.qrcode_dir / expected_filename
    target_path.write_bytes(b'existing-target')
    try:
      normalized = self.service._normalize_legacy_qrcode_path(
        f'/assets/qrcodes/{legacy_filename}',
        app_name='DeepSeek-V2',
        platform=Platform.WECHAT,
      )
      self.assertEqual(normalized, f'/assets/qrcodes/{expected_filename}')
      self.assertTrue(legacy_path.exists())
      self.assertTrue(target_path.exists())
    finally:
      if legacy_path.exists():
        legacy_path.unlink()
      if target_path.exists():
        target_path.unlink()

  def test_normalize_qrcode_path_renames_wecom_filename_to_wechat(self):
    legacy_filename = 'DeepSeek-V2_企业微信_abcd1234.png'
    legacy_path = self.service.settings.qrcode_dir / legacy_filename
    legacy_path.write_bytes(b'legacy-wecom')
    target_path = None
    try:
      normalized = self.service._normalize_legacy_qrcode_path(
        f'/assets/qrcodes/{legacy_filename}',
        app_name='DeepSeek-V2',
        platform=Platform.WECOM,
      )
      self.assertEqual(
        normalized,
        f'/assets/qrcodes/DeepSeek-V2_{self.service._safe_qrcode_name(Platform.WECHAT.value, fallback="platform")}_abcd1234.png',
      )
      target_path = self.service.settings.qrcode_dir / normalized.rsplit('/', 1)[-1]
      self.assertTrue(target_path.exists())
      self.assertFalse(legacy_path.exists())
    finally:
      if legacy_path.exists():
        legacy_path.unlink()
      if target_path is not None and target_path.exists():
        target_path.unlink()

  def test_load_cached_search_normalizes_legacy_qrcode_and_rewrites_cache(self):
    legacy_stem = hashlib.sha1(f'cache-legacy-{time.time_ns()}'.encode('utf-8')).hexdigest()
    legacy_filename = f'{legacy_stem}.png'
    legacy_path = self.service.settings.qrcode_dir / legacy_filename
    legacy_path.write_bytes(b'legacy-cache')
    cache_key = self.service._build_search_cache_key('deepseek', None)
    cached_card = ProductCard(
      product_id='deepseek-cache',
      app_name='DeepSeek-V2',
      description='cached',
      github_stars=1,
      created_at=None,
      verified_at=datetime.now(timezone.utc),
      groups=[
        {
          'group_id': 'group-cache',
          'platform': Platform.WECHAT,
          'group_type': GroupType.UNKNOWN,
          'entry': {'type': 'qrcode', 'image_path': f'/assets/qrcodes/{legacy_filename}'},
          'is_added': False,
          'source_urls': ['https://github.com/deepseek-ai/DeepSeek-V2'],
        },
      ],
      group_discovery_status=GroupDiscoveryStatus.FOUND,
      official_site_url=None,
      github_repo_url='https://github.com/deepseek-ai/DeepSeek-V2',
    )
    self.service._save_cached_search(cache_key, [cached_card])
    target_path = None
    try:
      cached = self.service._load_cached_search(cache_key)
      self.assertIsNotNone(cached)
      assert cached is not None
      normalized_path = cached[0].groups[0].entry.image_path
      self.assertNotIn(legacy_filename, normalized_path)
      target_filename = normalized_path.rsplit('/', 1)[-1]
      target_path = self.service.settings.qrcode_dir / target_filename
      self.assertTrue(target_path.exists())

      with get_connection(self.service.settings.database_path) as connection:
        row = connection.execute(
          'SELECT response_json FROM search_cache WHERE query_key = ?',
          (cache_key,),
        ).fetchone()
      self.assertIsNotNone(row)
      assert row is not None
      payload = json.loads(row['response_json'])
      self.assertEqual(
        payload[0]['groups'][0]['entry']['image_path'],
        normalized_path,
      )
    finally:
      if legacy_path.exists():
        legacy_path.unlink()
      if target_path is not None and target_path.exists():
        target_path.unlink()

  def test_list_viewed_groups_normalizes_legacy_qrcode_and_rewrites_db(self):
    legacy_stem = hashlib.sha1(f'viewed-legacy-{time.time_ns()}'.encode('utf-8')).hexdigest()
    legacy_filename = f'{legacy_stem}.png'
    legacy_path = self.service.settings.qrcode_dir / legacy_filename
    legacy_path.write_bytes(b'legacy-viewed')
    now = datetime.now(timezone.utc).isoformat()
    view_key = 'viewed-legacy-key'
    with get_connection(self.service.settings.database_path) as connection:
      connection.execute(
        '''
        INSERT INTO viewed_groups (
          view_key,
          product_id,
          app_name,
          platform,
          group_type,
          entry_type,
          entry_url,
          image_path,
          fallback_url,
          viewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
          view_key,
          'product-1',
          'DeepSeek-V2',
          Platform.WECHAT.value,
          GroupType.UNKNOWN.value,
          'qrcode',
          None,
          f'/assets/qrcodes/{legacy_filename}',
          None,
          now,
        ),
      )
      connection.commit()

    target_path = None
    try:
      groups = self.service.list_viewed_groups()
      self.assertEqual(len(groups), 1)
      normalized_path = groups[0].entry.image_path
      self.assertNotIn(legacy_filename, normalized_path)
      target_filename = normalized_path.rsplit('/', 1)[-1]
      target_path = self.service.settings.qrcode_dir / target_filename
      self.assertTrue(target_path.exists())

      with get_connection(self.service.settings.database_path) as connection:
        row = connection.execute(
          'SELECT image_path FROM viewed_groups WHERE view_key = ?',
          (view_key,),
        ).fetchone()
      self.assertIsNotNone(row)
      assert row is not None
      self.assertEqual(row['image_path'], normalized_path)
    finally:
      if legacy_path.exists():
        legacy_path.unlink()
      if target_path is not None and target_path.exists():
        target_path.unlink()

  def test_list_viewed_groups_canonicalizes_legacy_wecom_platform_and_rewrites_db(self):
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(self.service.settings.database_path) as connection:
      connection.execute(
        '''
        INSERT INTO viewed_groups (
          view_key,
          product_id,
          app_name,
          platform,
          group_type,
          entry_type,
          entry_url,
          image_path,
          fallback_url,
          viewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
          'legacy-wecom-view',
          'product-legacy',
          'Legacy Tool',
          Platform.WECOM.value,
          GroupType.UNKNOWN.value,
          'link',
          'https://work.weixin.qq.com/gm/legacy',
          None,
          'https://work.weixin.qq.com/gm/legacy',
          now,
        ),
      )
      connection.commit()

    groups = self.service.list_viewed_groups()

    self.assertEqual(len(groups), 1)
    self.assertEqual(groups[0].platform, Platform.WECHAT)
    with get_connection(self.service.settings.database_path) as connection:
      row = connection.execute(
        'SELECT platform FROM viewed_groups WHERE view_key = ?',
        ('legacy-wecom-view',),
      ).fetchone()
    self.assertIsNotNone(row)
    assert row is not None
    self.assertEqual(row['platform'], Platform.WECHAT.value)

  def test_github_search_filters_noisy_repos(self):
    noisy = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/awesome-chatgpt',
      full_name='example/awesome-chatgpt',
      repo_name='awesome-chatgpt',
      owner_name='example',
      owner_type='User',
      homepage=None,
      description='awesome list of chatgpt prompts',
      stars=10,
      is_fork=False,
      archived=False,
      disabled=False,
    )
    self.assertTrue(self.service._should_filter(noisy))

  def test_github_search_adds_community_variants_for_generic_keyword(self):
    captured_queries: list[tuple[str, int]] = []

    def fake_search(variant_query: str, per_page: int):
      captured_queries.append((variant_query, per_page))
      return []

    with patch.object(self.service, '_github_search_onevariant', side_effect=fake_search):
      self.service._github_search('bot', limit=20)

    queried_texts = [item[0] for item in captured_queries]
    self.assertTrue(any('discord' in query for query in queried_texts))
    self.assertTrue(any('qq' in query for query in queried_texts))
    self.assertTrue(any(query.endswith('in:name') and per_page >= 24 for query, per_page in captured_queries))

  def test_fetch_candidate_pages_limits_related_pages(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/labring/FastGPT',
      full_name='labring/FastGPT',
      repo_name='FastGPT',
      owner_name='labring',
      owner_type='Organization',
      homepage='https://fastgpt.io/en',
      description='FastGPT',
      stars=100,
    )
    html_by_url = {
      'https://github.com/labring/FastGPT': (
        '<html><body>'
        '<a href="/labring/FastGPT/discussions">Community</a>'
        '<a href="https://fastgpt.io/docs">Docs</a>'
        '</body></html>'
      ),
      'https://github.com/labring/FastGPT/discussions': '<html><body>Community</body></html>',
    }

    def fake_fetch(url: str):
      html = html_by_url.get(url.rstrip('/'))
      if html is None:
        return None
      return FetchedPage(
        requested_url=url,
        final_url=url.rstrip('/'),
        html=html,
        title='Test',
        text='',
      )

    with patch.object(self.service, '_fetch_page', side_effect=fake_fetch):
      pages = self.service._fetch_candidate_pages(candidate)

    fetched_urls = {page.final_url for page in pages}
    self.assertIn('https://github.com/labring/FastGPT', fetched_urls)
    self.assertIn('https://github.com/labring/FastGPT/discussions', fetched_urls)
    self.assertEqual(len(fetched_urls), 2)

  def test_fetch_candidate_pages_includes_homepage_seed(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/n8n-io/n8n',
      full_name='n8n-io/n8n',
      repo_name='n8n',
      owner_name='n8n-io',
      owner_type='Organization',
      homepage='https://n8n.io',
      description='n8n',
      stars=100,
    )
    html_by_url = {
      'https://github.com/n8n-io/n8n': '<html><body>Repo</body></html>',
      'https://n8n.io': (
        '<html><body>'
        '<a href="/community">Join community</a>'
        '</body></html>'
      ),
      'https://n8n.io/community': '<html><body>Community page</body></html>',
    }

    def fake_fetch(url: str):
      html = html_by_url.get(url.rstrip('/'))
      if html is None:
        return None
      return FetchedPage(
        requested_url=url,
        final_url=url.rstrip('/'),
        html=html,
        title='Test',
        text='',
      )

    with patch.object(self.service, '_fetch_page', side_effect=fake_fetch):
      pages = self.service._fetch_candidate_pages(candidate)

    fetched_urls = {page.final_url for page in pages}
    self.assertIn('https://github.com/n8n-io/n8n', fetched_urls)
    self.assertIn('https://n8n.io', fetched_urls)
    self.assertIn('https://n8n.io/community', fetched_urls)

  def test_collect_relevant_links_filters_github_global_noise(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/coze-dev/coze-studio',
      full_name='coze-dev/coze-studio',
      repo_name='coze-studio',
      owner_name='coze-dev',
      owner_type='Organization',
      homepage=None,
      description='coze',
      stars=100,
    )
    page = FetchedPage(
      requested_url='https://github.com/coze-dev/coze-studio',
      final_url='https://github.com/coze-dev/coze-studio',
      html=(
        '<html><body>'
        '<a href="/coze-dev/coze-studio/discussions">Community</a>'
        '<a href="/orgs/community/discussions">Community discussions</a>'
        '<a href="https://support.github.com/request/landing">Support</a>'
        '<a href="https://maintainers.github.com/auth/signin">Maintainers</a>'
        '<a href="https://github.com/premium-support">Premium Support</a>'
        '</body></html>'
      ),
      title='coze',
      text='',
    )

    links = self.service._collect_relevant_links(page, candidate)

    self.assertIn('https://github.com/coze-dev/coze-studio/discussions', links)
    self.assertFalse(any('/orgs/community/' in link for link in links))
    self.assertFalse(any('support.github.com' in link for link in links))
    self.assertFalse(any('maintainers.github.com' in link for link in links))
    self.assertFalse(any('premium-support' in link for link in links))

  def test_collect_relevant_links_supports_chinese_community_anchor(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage=None,
      description='repo',
      stars=20,
    )
    page = FetchedPage(
      requested_url='https://github.com/example/repo',
      final_url='https://github.com/example/repo',
      html=(
        '<html><body>'
        '<a href="/example/repo/discussions">加入交流群</a>'
        '</body></html>'
      ),
      title='repo',
      text='',
    )

    links = self.service._collect_relevant_links(page, candidate)
    self.assertIn('https://github.com/example/repo/discussions', links)

  def test_collect_relevant_links_supports_image_badge_anchor(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=20,
    )
    page = FetchedPage(
      requested_url='https://repo.example.com',
      final_url='https://repo.example.com',
      html=(
        '<html><body>'
        '<a href="/community/qrcode">'
        '<img alt="wechat community qrcode" src="https://img.shields.io/badge/WeChat-Join-brightgreen?logo=wechat" />'
        '</a>'
        '</body></html>'
      ),
      title='repo',
      text='',
    )

    links = self.service._collect_relevant_links(page, candidate)
    self.assertIn('https://repo.example.com/community/qrcode', links)

  def test_collect_relevant_links_reuses_cached_soup(self):
    html = (
      '<html><body>'
      '<a href="/example/repo/discussions">Community</a>'
      '</body></html>'
    )
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage=None,
      description='repo',
      stars=20,
    )
    page = FetchedPage(
      requested_url='https://github.com/example/repo',
      final_url='https://github.com/example/repo',
      html=html,
      title='repo',
      text='',
      soup=BeautifulSoup(html, 'html.parser'),
    )

    with patch('app.search.service.BeautifulSoup', side_effect=AssertionError('should reuse cached soup')):
      links = self.service._collect_relevant_links(page, candidate)

    self.assertEqual(links, ['https://github.com/example/repo/discussions'])

  def test_collect_cards_reuses_page_fetch_cache_for_duplicate_urls(self):
    first_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    second_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo-mirror',
      repo_name='repo-mirror',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo mirror',
      stars=9,
    )

    def fake_fetch_page(url: str):
      normalized = url.rstrip('/')
      if normalized == 'https://github.com/example/repo':
        html = '<html><body>repo</body></html>'
      elif normalized == 'https://repo.example.com':
        html = '<html><body>homepage</body></html>'
      else:
        return None
      return FetchedPage(
        requested_url=url,
        final_url=normalized,
        html=html,
        title='Test',
        text='',
      )

    with patch.object(self.service, '_fetch_page', side_effect=fake_fetch_page) as fetch_page_mock, patch.object(
      self.service.extractor,
      'extract',
      return_value=[],
    ):
      self.service._collect_cards([first_candidate, second_candidate])

    self.assertEqual(fetch_page_mock.call_count, 2)

  def test_build_crawl_candidates_uses_only_github_candidates(self):
    candidates = [
      GitHubRepositoryCandidate(
        repo_url=f'https://github.com/example/repo-{index}',
        full_name=f'example/repo-{index}',
        repo_name=f'repo-{index}',
        owner_name='example',
        owner_type='Organization',
        homepage=f'https://repo-{index}.example.com',
        description='Example repo',
        stars=100 - index,
      )
      for index in range(12)
    ]

    crawl_candidates = self.service._build_crawl_candidates('repo', candidates)

    self.assertEqual(len(crawl_candidates), 10)
    self.assertEqual(
      [candidate.repo_url for candidate in crawl_candidates],
      [candidate.repo_url for candidate in candidates[:10]],
    )

  def test_build_crawl_candidates_expands_related_when_primary_insufficient(self):
    primary = [
      GitHubRepositoryCandidate(
        repo_url='https://github.com/example/repo-1',
        full_name='example/repo-1',
        repo_name='repo-1',
        owner_name='example',
        owner_type='Organization',
        homepage='https://repo-1.example.com',
        description='repo 1',
        stars=100,
      ),
    ]
    expanded = [
      GitHubRepositoryCandidate(
        repo_url='https://github.com/example/repo-2',
        full_name='example/repo-2',
        repo_name='repo-2',
        owner_name='example',
        owner_type='Organization',
        homepage='https://repo-2.example.com',
        description='repo 2',
        stars=90,
      ),
    ]

    with patch.object(self.service, '_expand_related_candidates', return_value=expanded) as expand_mock:
      crawl_candidates = self.service._build_crawl_candidates('repo', primary)

    self.assertEqual(len(crawl_candidates), 2)
    self.assertEqual(crawl_candidates[0].repo_name, 'repo-1')
    self.assertEqual(crawl_candidates[1].repo_name, 'repo-2')
    expand_mock.assert_called_once()

  def test_search_returns_github_result_for_keyword(self):
    github_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/n8n-io/n8n',
      full_name='n8n-io/n8n',
      repo_name='n8n',
      owner_name='n8n-io',
      owner_type='Organization',
      homepage='https://n8n.io',
      description='n8n',
      stars=100,
      created_at='2024-01-01T00:00:00Z',
    )
    github_page = FetchedPage(
      'https://github.com/n8n-io/n8n',
      'https://github.com/n8n-io/n8n',
      '<html></html>',
      'n8n',
      '',
    )
    discord_group = ExtractedGroupCandidate(
      platform=Platform.DISCORD,
      group_type=GroupType.UNKNOWN,
      source_url='https://github.com/n8n-io/n8n',
      context='community discord server',
      entry_url='https://discord.gg/n8n',
      fallback_url='https://discord.gg/n8n',
      source_urls=['https://github.com/n8n-io/n8n'],
    )

    with patch.object(self.service, '_github_search', return_value=[github_candidate]), patch.object(
      self.service,
      '_fetch_candidate_pages',
      return_value=[github_page],
    ), patch.object(
      self.service,
      '_expand_related_candidates',
      return_value=[],
    ), patch.object(
      self.service,
      '_build_web_fallback_candidates',
      return_value=[],
    ), patch.object(
      self.service.extractor,
      'extract',
      return_value=[discord_group],
    ):
      results = self.service.search('n8n')

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].github_repo_url, 'https://github.com/n8n-io/n8n')
    self.assertEqual(results[0].groups[0].platform, Platform.DISCORD)

  def test_search_same_query_reads_from_persistent_cache(self):
    first_results = [self._make_card(index) for index in range(3)]
    cache_key = self.service._build_search_cache_key('n8n-cache', None)
    self.service._save_cached_search(cache_key, first_results)
    with patch.object(self.service, '_github_search') as github_search_mock:
      second_results = self.service.search('n8n-cache', limit=3)

    self.assertEqual(len(first_results), 3)
    self.assertEqual(len(second_results), 3)
    github_search_mock.assert_not_called()

  def test_search_uses_web_fallback_when_github_pages_have_no_groups(self):
    github_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/n8n-io/n8n',
      full_name='n8n-io/n8n',
      repo_name='n8n',
      owner_name='n8n-io',
      owner_type='Organization',
      homepage='https://n8n.io',
      description='n8n',
      stars=100,
    )
    web_candidate = GitHubRepositoryCandidate(
      repo_url=None,
      full_name='web/n8n.io',
      repo_name='n8n',
      owner_name='n8n.io',
      owner_type='Website',
      homepage='https://n8n.io',
      description='n8n official',
      stars=0,
    )
    repo_page = FetchedPage(
      'https://github.com/n8n-io/n8n',
      'https://github.com/n8n-io/n8n',
      '<html><body><h1>Repo</h1></body></html>',
      'n8n',
      '',
    )
    official_page = FetchedPage(
      'https://n8n.io',
      'https://n8n.io',
      '<html><body><a href="https://discord.com/invite/n8n">Join community</a></body></html>',
      'n8n',
      '',
    )
    discord_group = ExtractedGroupCandidate(
      platform=Platform.DISCORD,
      group_type=GroupType.UNKNOWN,
      source_url='https://n8n.io',
      context='join community discord',
      entry_url='https://discord.com/invite/n8n',
      fallback_url='https://discord.com/invite/n8n',
      source_urls=['https://n8n.io'],
    )

    def fake_fetch_pages(candidate: GitHubRepositoryCandidate, **kwargs):
      del kwargs
      if candidate.full_name == 'web/n8n.io':
        return [official_page]
      return [repo_page]

    def fake_extract(pages: list[FetchedPage]):
      if pages and pages[0].final_url == 'https://n8n.io':
        return [discord_group]
      return []

    with patch.object(self.service, '_github_search', return_value=[github_candidate]), patch.object(
      self.service,
      '_fetch_candidate_pages',
      side_effect=fake_fetch_pages,
    ), patch.object(
      self.service,
      '_expand_related_candidates',
      return_value=[],
    ), patch.object(
      self.service,
      '_build_web_fallback_candidates',
      return_value=[web_candidate],
    ), patch.object(
      self.service.extractor,
      'extract',
      side_effect=fake_extract,
    ):
      results = self.service.search('n8n')

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].official_site_url, 'https://n8n.io')
    self.assertEqual(results[0].groups[0].platform, Platform.DISCORD)

  def test_search_merges_github_and_web_candidates_in_one_pass(self):
    github_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/org/minimax',
      full_name='org/minimax',
      repo_name='minimax',
      owner_name='org',
      owner_type='Organization',
      homepage='https://minimax.com',
      description='minimax',
      stars=100,
    )
    web_candidate = GitHubRepositoryCandidate(
      repo_url=None,
      full_name='web/minimax.com',
      repo_name='minimax',
      owner_name='minimax.com',
      owner_type='Website',
      homepage='https://minimax.com/community',
      description='minimax community',
      stars=0,
    )

    with patch.object(self.service, '_github_search', return_value=[github_candidate]), patch.object(
      self.service,
      '_build_crawl_candidates',
      return_value=[github_candidate],
    ), patch.object(
      self.service,
      '_build_web_fallback_candidates',
      return_value=[web_candidate],
    ), patch.object(
      self.service,
      '_collect_cards',
      return_value=[],
    ) as collect_cards_mock:
      results = self.service.search('minimax', refresh=True, limit=10)

    self.assertEqual(results, [])
    merged = collect_cards_mock.call_args.args[0]
    self.assertEqual([candidate.full_name for candidate in merged], ['org/minimax', 'web/minimax.com'])

  def test_search_sorts_web_fallback_candidates_by_query_relevance(self):
    fallback_candidates = [
      GitHubRepositoryCandidate(
        repo_url=None,
        full_name='web/minimax.ad',
        repo_name='minimax',
        owner_name='minimax.ad',
        owner_type='Website',
        homepage='https://minimax.ad',
        description='minimax ad',
        stars=0,
      ),
      GitHubRepositoryCandidate(
        repo_url=None,
        full_name='web/www.minimax.io',
        repo_name='minimax',
        owner_name='www.minimax.io',
        owner_type='Website',
        homepage='https://www.minimax.io',
        description='minimax io',
        stars=0,
      ),
      GitHubRepositoryCandidate(
        repo_url=None,
        full_name='web/www.minimaxi.com',
        repo_name='minimaxi',
        owner_name='www.minimaxi.com',
        owner_type='Website',
        homepage='https://www.minimaxi.com',
        description='minimaxi com',
        stars=0,
      ),
      GitHubRepositoryCandidate(
        repo_url=None,
        full_name='web/www.minimax.com',
        repo_name='minimax',
        owner_name='www.minimax.com',
        owner_type='Website',
        homepage='https://www.minimax.com',
        description='minimax com',
        stars=0,
      ),
    ]

    with patch.object(self.service, '_github_search', return_value=[]), patch.object(
      self.service,
      '_build_crawl_candidates',
      return_value=[],
    ), patch.object(
      self.service,
      '_build_web_fallback_candidates',
      return_value=fallback_candidates,
    ), patch.object(
      self.service,
      '_collect_cards',
      return_value=[],
    ) as collect_cards_mock:
      self.service.search('minimax', refresh=True, limit=5)

    merged = collect_cards_mock.call_args.args[0]
    self.assertEqual(
      [candidate.full_name for candidate in merged],
      [
        'web/www.minimax.com',
        'web/www.minimaxi.com',
        'web/www.minimax.io',
        'web/minimax.ad',
      ],
    )

  def test_collect_cards_stops_after_first_batch_when_max_cards_reached(self):
    candidates = [
      GitHubRepositoryCandidate(
        repo_url=f'https://github.com/org/tool-{index}',
        full_name=f'org/tool-{index}',
        repo_name=f'tool-{index}',
        owner_name='org',
        owner_type='Organization',
        homepage=None,
        description='tool',
        stars=100 - index,
      )
      for index in range(8)
    ]

    def fake_fetch_candidate_pages(candidate: GitHubRepositoryCandidate, **kwargs):
      del kwargs
      page = FetchedPage(
        requested_url=candidate.repo_url or '',
        final_url=(candidate.repo_url or '').rstrip('/'),
        html='<html></html>',
        title='tool',
        text='',
      )
      return [page]

    def fake_extract(pages: list[FetchedPage]):
      suffix = pages[0].final_url.rsplit('/', 1)[-1]
      link = f'https://discord.gg/{suffix}'
      return [
        ExtractedGroupCandidate(
          platform=Platform.DISCORD,
          group_type=GroupType.UNKNOWN,
          source_url=pages[0].final_url,
          context='discord community',
          entry_url=link,
          fallback_url=link,
          source_urls=[pages[0].final_url],
        ),
      ]

    with patch.object(self.service, '_fetch_candidate_pages', side_effect=fake_fetch_candidate_pages) as fetch_mock, patch.object(
      self.service.extractor,
      'extract',
      side_effect=fake_extract,
    ):
      cards = self.service._collect_cards(candidates, max_cards=1)

    self.assertEqual(len(cards), 1)
    self.assertLess(fetch_mock.call_count, len(candidates))

  def test_build_product_card_supports_qq_number_entry(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/itchat',
      full_name='example/itchat',
      repo_name='itchat',
      owner_name='example',
      owner_type='User',
      homepage='https://itchat.example.com',
      description='itchat',
      stars=500,
    )
    qq_group = ExtractedGroupCandidate(
      platform=Platform.QQ,
      group_type=GroupType.UNKNOWN,
      source_url='https://github.com/example/itchat',
      context='QQ群讨论：549762872',
      entry_url=None,
      fallback_url='https://github.com/example/itchat',
      qq_number='549762872',
      source_urls=['https://github.com/example/itchat'],
    )

    card = self.service._build_product_card(candidate, [qq_group])

    self.assertEqual(len(card.groups), 1)
    self.assertIsInstance(card.groups[0].entry, QQNumberEntry)
    self.assertEqual(card.groups[0].entry.qq_number, '549762872')

  def test_group_signature_merges_wecom_and_wechat_links(self):
    wecom_group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://example.com/community',
      context='work wechat',
      entry_url='https://work.weixin.qq.com/gm/shared-room?utm_source=test',
      fallback_url='https://work.weixin.qq.com/gm/shared-room?utm_source=test',
      source_urls=['https://example.com/community'],
    )
    wechat_group = ExtractedGroupCandidate(
      platform=Platform.WECHAT,
      group_type=GroupType.UNKNOWN,
      source_url='https://example.com/community',
      context='wechat',
      entry_url='https://work.weixin.qq.com/gm/shared-room',
      fallback_url='https://work.weixin.qq.com/gm/shared-room',
      source_urls=['https://example.com/community'],
    )

    self.assertEqual(
      self.service._group_signature(wecom_group),
      self.service._group_signature(wechat_group),
    )

  def test_build_product_card_canonicalizes_wecom_platform_to_wechat(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/wecom-tool',
      full_name='example/wecom-tool',
      repo_name='wecom-tool',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      entry_url='https://work.weixin.qq.com/gm/canonical',
      fallback_url='https://work.weixin.qq.com/gm/canonical',
      qrcode_verified=False,
      source_urls=['https://repo.example.com/community'],
    )

    card = self.service._build_product_card(candidate, [group])

    self.assertEqual(len(card.groups), 1)
    self.assertEqual(card.groups[0].platform, Platform.WECHAT)

  def test_github_search_prioritizes_exact_repo_name_over_stars(self):
    exact = GitHubRepositoryCandidate(
      repo_url='https://github.com/labring/FastGPT',
      full_name='labring/FastGPT',
      repo_name='FastGPT',
      owner_name='labring',
      owner_type='Organization',
      homepage='https://fastgpt.io',
      description='FastGPT',
      stars=2_000,
    )
    high_star_noise = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/super-gpt',
      full_name='example/super-gpt',
      repo_name='super-gpt',
      owner_name='example',
      owner_type='Organization',
      homepage=None,
      description='general gpt toolkit',
      stars=80_000,
    )
    partial = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/fastgpt-helper',
      full_name='example/fastgpt-helper',
      repo_name='fastgpt-helper',
      owner_name='example',
      owner_type='Organization',
      homepage=None,
      description='helper',
      stars=500,
    )

    with patch.object(
      self.service,
      '_github_search_onevariant',
      return_value=[high_star_noise, exact, partial],
    ):
      results = self.service._github_search('FastGPT', limit=3)

    self.assertEqual(results[0].full_name, 'labring/FastGPT')

  def test_collect_cards_dedupes_same_group_across_cards(self):
    first_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/org/tool-a',
      full_name='org/tool-a',
      repo_name='tool-a',
      owner_name='org',
      owner_type='Organization',
      homepage='https://tool-a.example.com',
      description='tool a',
      stars=100,
    )
    second_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/org/tool-b',
      full_name='org/tool-b',
      repo_name='tool-b',
      owner_name='org',
      owner_type='Organization',
      homepage='https://tool-b.example.com',
      description='tool b',
      stars=99,
    )
    pages = [
      FetchedPage('https://github.com/org/tool-a', 'https://github.com/org/tool-a', '<html></html>', 'A', ''),
      FetchedPage('https://github.com/org/tool-b', 'https://github.com/org/tool-b', '<html></html>', 'B', ''),
    ]
    duplicated_link = 'https://discord.com/invite/shared-room'
    groups = [
      ExtractedGroupCandidate(
        platform=Platform.DISCORD,
        group_type=GroupType.UNKNOWN,
        source_url='https://github.com/org/tool-a',
        context='discord',
        entry_url=duplicated_link,
        fallback_url=duplicated_link,
        source_urls=['https://github.com/org/tool-a'],
      ),
      ExtractedGroupCandidate(
        platform=Platform.DISCORD,
        group_type=GroupType.UNKNOWN,
        source_url='https://github.com/org/tool-b',
        context='discord',
        entry_url=f'{duplicated_link}?utm_source=test',
        fallback_url=f'{duplicated_link}?utm_source=test',
        source_urls=['https://github.com/org/tool-b'],
      ),
    ]

    def fake_fetch(candidate: GitHubRepositoryCandidate, **kwargs):
      del kwargs
      return [pages[0]] if candidate.repo_name == 'tool-a' else [pages[1]]

    def fake_extract(input_pages: list[FetchedPage]):
      return [groups[0]] if input_pages[0].final_url.endswith('tool-a') else [groups[1]]

    with patch.object(self.service, '_fetch_candidate_pages', side_effect=fake_fetch), patch.object(
      self.service.extractor,
      'extract',
      side_effect=fake_extract,
    ):
      cards = self.service._collect_cards([first_candidate, second_candidate])

    self.assertEqual(len(cards), 1)

  def test_collect_cards_skips_card_without_valid_groups(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    invalid_group = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='feishu official group',
      entry_url='https://open.feishu.cn/share/base/form/abc',
      fallback_url='https://open.feishu.cn/share/base/form/abc',
      qrcode_verified=False,
      source_urls=['https://repo.example.com/community'],
    )

    with patch.object(
      self.service,
      '_fetch_candidate_pages',
      return_value=[FetchedPage('https://repo.example.com', 'https://repo.example.com', '<html></html>', 'repo', '')],
    ), patch.object(
      self.service.extractor,
      'extract',
      return_value=[invalid_group],
    ):
      cards = self.service._collect_cards([candidate])

    self.assertEqual(cards, [])

  def test_collect_cards_keeps_candidate_order_with_parallel_fetch(self):
    candidates = [
      GitHubRepositoryCandidate(
        repo_url=f'https://github.com/example/{name}',
        full_name=f'example/{name}',
        repo_name=name,
        owner_name='example',
        owner_type='Organization',
        homepage=f'https://{name}.example.com',
        description=name,
        stars=10,
      )
      for name in ('slow', 'fast-a', 'fast-b')
    ]

    def fake_fetch(candidate: GitHubRepositoryCandidate, **kwargs):
      del kwargs
      if candidate.repo_name == 'slow':
        time.sleep(0.05)
      return [
        FetchedPage(
          requested_url=candidate.repo_url or '',
          final_url=f'https://example.com/{candidate.repo_name}',
          html='<html></html>',
          title=candidate.repo_name,
          text='',
        ),
      ]

    def fake_extract(input_pages: list[FetchedPage]):
      repo_name = input_pages[0].final_url.rstrip('/').split('/')[-1]
      invite = f'https://discord.com/invite/{repo_name}'
      return [
        ExtractedGroupCandidate(
          platform=Platform.DISCORD,
          group_type=GroupType.UNKNOWN,
          source_url=input_pages[0].final_url,
          context='discord community group',
          entry_url=invite,
          fallback_url=invite,
          source_urls=[input_pages[0].final_url],
        ),
      ]

    with patch.object(self.service, '_fetch_candidate_pages', side_effect=fake_fetch), patch.object(
      self.service.extractor,
      'extract',
      side_effect=fake_extract,
    ):
      cards = self.service._collect_cards(candidates)

    self.assertEqual([card.app_name for card in cards], ['slow', 'fast-a', 'fast-b'])

  def test_collect_cards_uses_browser_fallback_when_official_groups_missing(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://official.minimax.example',
      description='repo',
      stars=10,
    )
    static_pages = [
      FetchedPage(
        requested_url='https://github.com/example/repo',
        final_url='https://github.com/example/repo',
        html='<html></html>',
        title='repo',
        text='',
      ),
      FetchedPage(
        requested_url='https://official.minimax.example',
        final_url='https://official.minimax.example',
        html='<html></html>',
        title='official',
        text='',
      ),
    ]
    browser_page = FetchedPage(
      requested_url='https://official.minimax.example',
      final_url='https://official.minimax.example',
      html='<html></html>',
      title='official',
      text='',
      fetch_method='browser',
    )
    github_group = ExtractedGroupCandidate(
      platform=Platform.DISCORD,
      group_type=GroupType.UNKNOWN,
      source_url='https://github.com/example/repo',
      context='discord community',
      entry_url='https://discord.com/invite/example-repo',
      fallback_url='https://discord.com/invite/example-repo',
      source_urls=['https://github.com/example/repo'],
    )
    official_group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://official.minimax.example',
      context='wechat group',
      entry_url='https://work.weixin.qq.com/gm/official',
      fallback_url='https://work.weixin.qq.com/gm/official',
      source_urls=['https://official.minimax.example'],
    )

    def fake_extract(input_pages: list[FetchedPage]):
      if any(page.fetch_method == 'browser' for page in input_pages):
        return [official_group]
      return [github_group]

    with patch.object(self.service, '_fetch_candidate_pages', return_value=static_pages), patch.object(
      self.service,
      '_fetch_page_with_browser',
      return_value=browser_page,
    ) as browser_fetch_mock, patch.object(
      self.service.extractor,
      'extract',
      side_effect=fake_extract,
    ):
      cards = self.service._collect_cards([candidate], max_cards=1)

    self.assertEqual(len(cards), 1)
    browser_fetch_mock.assert_called_once_with('https://official.minimax.example')
    source_domains = {
      self.service._domain_key(source_url)
      for group in cards[0].groups
      for source_url in group.source_urls
    }
    self.assertIn('minimax.example', source_domains)

  def test_collect_cards_skips_browser_fallback_when_official_group_found_in_static_pages(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://official.minimax.example',
      description='repo',
      stars=10,
    )
    static_pages = [
      FetchedPage(
        requested_url='https://official.minimax.example',
        final_url='https://official.minimax.example',
        html='<html></html>',
        title='official',
        text='',
      ),
    ]
    official_group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://official.minimax.example',
      context='wechat group',
      entry_url='https://work.weixin.qq.com/gm/official',
      fallback_url='https://work.weixin.qq.com/gm/official',
      source_urls=['https://official.minimax.example'],
    )

    with patch.object(self.service, '_fetch_candidate_pages', return_value=static_pages), patch.object(
      self.service,
      '_fetch_page_with_browser',
    ) as browser_fetch_mock, patch.object(
      self.service.extractor,
      'extract',
      return_value=[official_group],
    ):
      cards = self.service._collect_cards([candidate], max_cards=1)

    self.assertEqual(len(cards), 1)
    browser_fetch_mock.assert_not_called()

  def test_collect_cards_triggers_browser_fallback_when_static_groups_only_from_subdomain(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://www.minimaxi.com',
      description='repo',
      stars=10,
    )
    static_pages = [
      FetchedPage(
        requested_url='https://www.minimaxi.com',
        final_url='https://www.minimaxi.com',
        html='<html></html>',
        title='official',
        text='',
      ),
      FetchedPage(
        requested_url='https://platform.minimaxi.com/docs',
        final_url='https://platform.minimaxi.com/docs',
        html='<html></html>',
        title='docs',
        text='',
      ),
    ]
    browser_page = FetchedPage(
      requested_url='https://www.minimaxi.com',
      final_url='https://www.minimaxi.com',
      html='<html></html>',
      title='official',
      text='',
      fetch_method='browser',
    )
    static_subdomain_group = ExtractedGroupCandidate(
      platform=Platform.DISCORD,
      group_type=GroupType.UNKNOWN,
      source_url='https://platform.minimaxi.com/docs',
      context='discord community',
      entry_url='https://discord.gg/minimax',
      fallback_url='https://discord.gg/minimax',
      source_urls=['https://platform.minimaxi.com/docs'],
    )
    browser_homepage_group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://www.minimaxi.com',
      context='wechat group',
      entry_url='https://work.weixin.qq.com/gm/official',
      fallback_url='https://work.weixin.qq.com/gm/official',
      source_urls=['https://www.minimaxi.com'],
    )

    def fake_extract(input_pages: list[FetchedPage]):
      if any(page.fetch_method == 'browser' for page in input_pages):
        return [browser_homepage_group]
      return [static_subdomain_group]

    with patch.object(self.service, '_fetch_candidate_pages', return_value=static_pages), patch.object(
      self.service,
      '_fetch_page_with_browser',
      return_value=browser_page,
    ) as browser_fetch_mock, patch.object(
      self.service.extractor,
      'extract',
      side_effect=fake_extract,
    ):
      cards = self.service._collect_cards([candidate], max_cards=1)

    self.assertEqual(len(cards), 1)
    browser_fetch_mock.assert_called_once_with('https://www.minimaxi.com')

  def test_collect_cards_browser_fallback_failure_degrades_gracefully(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://official.minimax.example',
      description='repo',
      stars=10,
    )
    static_pages = [
      FetchedPage(
        requested_url='https://github.com/example/repo',
        final_url='https://github.com/example/repo',
        html='<html></html>',
        title='repo',
        text='',
      ),
      FetchedPage(
        requested_url='https://official.minimax.example',
        final_url='https://official.minimax.example',
        html='<html></html>',
        title='official',
        text='',
      ),
    ]
    github_group = ExtractedGroupCandidate(
      platform=Platform.DISCORD,
      group_type=GroupType.UNKNOWN,
      source_url='https://github.com/example/repo',
      context='discord community',
      entry_url='https://discord.com/invite/example-repo',
      fallback_url='https://discord.com/invite/example-repo',
      source_urls=['https://github.com/example/repo'],
    )

    with patch.object(self.service, '_fetch_candidate_pages', return_value=static_pages), patch.object(
      self.service,
      '_fetch_page_with_browser',
      return_value=None,
    ) as browser_fetch_mock, patch.object(
      self.service.extractor,
      'extract',
      return_value=[github_group],
    ):
      cards = self.service._collect_cards([candidate], max_cards=1)

    self.assertEqual(len(cards), 1)
    browser_fetch_mock.assert_called_once_with('https://official.minimax.example')

  def test_dedupe_groups_prefers_image_hash_and_merges_sources(self):
    image_bytes = make_image_bytes(520, 520)
    first = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.UNKNOWN,
      source_url='https://fastgpt.io/community',
      context='官方群',
      image_url='https://fastgpt.io/qr.png',
      image_bytes=image_bytes,
      image_content_type='image/png',
      source_urls=['https://fastgpt.io/community'],
    )
    second = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.UNKNOWN,
      source_url='https://github.com/labring/FastGPT',
      context='官方群',
      image_url='https://github.com/labring/FastGPT/raw/main/qr.png',
      image_bytes=image_bytes,
      image_content_type='image/png',
      source_urls=['https://github.com/labring/FastGPT'],
    )

    deduped = self.service._dedupe_groups([first, second])
    self.assertEqual(len(deduped), 1)
    self.assertCountEqual(
      deduped[0].source_urls,
      ['https://fastgpt.io/community', 'https://github.com/labring/FastGPT'],
    )

  def test_build_product_card_uses_link_for_unverified_image_candidate(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      image_url='https://repo.example.com/qr.png',
      image_bytes=make_image_bytes(520, 520),
      image_content_type='image/png',
      entry_url='https://open.feishu.cn/community',
      fallback_url='https://open.feishu.cn/community',
      qrcode_verified=False,
      source_urls=['https://repo.example.com/community'],
    )

    card = self.service._build_product_card(candidate, [group])
    self.assertEqual(len(card.groups), 1)
    self.assertEqual(card.groups[0].entry.type, 'link')

  def test_dedupe_feishu_form_links_by_canonical_path(self):
    first = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.UNKNOWN,
      source_url='https://fastgpt.io',
      context='community',
      entry_url='https://fael3z0zfze.feishu.cn/share/base/form/shrcnjJWtKqjOI9NbQTzhNyzljc?prefill_S=C2&hide_S=1',
      fallback_url='https://fael3z0zfze.feishu.cn/share/base/form/shrcnjJWtKqjOI9NbQTzhNyzljc?prefill_S=C2&hide_S=1',
      qrcode_verified=False,
      source_urls=['https://fastgpt.io'],
    )
    second = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.UNKNOWN,
      source_url='https://github.com/labring/FastGPT',
      context='community',
      entry_url='https://fael3z0zfze.feishu.cn/share/base/form/shrcnjJWtKqjOI9NbQTzhNyzljc',
      fallback_url='https://fael3z0zfze.feishu.cn/share/base/form/shrcnjJWtKqjOI9NbQTzhNyzljc',
      qrcode_verified=False,
      source_urls=['https://github.com/labring/FastGPT'],
    )

    deduped = self.service._dedupe_groups([first, second])
    self.assertEqual(len(deduped), 1)

  def test_viewed_group_is_filtered_from_results(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.DISCORD,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      entry_url='https://discord.com/invite/repo',
      fallback_url='https://discord.com/invite/repo',
      qrcode_verified=False,
      source_urls=['https://repo.example.com/community'],
    )

    card = self.service._build_product_card(candidate, [group])
    self.assertEqual(len(card.groups), 1)
    self.service.mark_group_viewed(card.product_id, card.app_name, card.groups[0])
    filtered = self.service._filter_viewed_cards([card])
    self.assertEqual(filtered, [])

  def test_legacy_wecom_viewed_group_still_filters_canonical_wechat_result(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/wecom-filter',
      full_name='example/wecom-filter',
      repo_name='wecom-filter',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      entry_url='https://work.weixin.qq.com/gm/legacy-filter',
      fallback_url='https://work.weixin.qq.com/gm/legacy-filter',
      qrcode_verified=False,
      source_urls=['https://repo.example.com/community'],
    )

    legacy_group_id = hashlib.sha1(
      f'{candidate.full_name.lower()}:{Platform.WECOM.value}:link:https://work.weixin.qq.com/gm/legacy-filter'.encode('utf-8'),
    ).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(self.service.settings.database_path) as connection:
      connection.execute(
        '''
        INSERT INTO viewed_groups (
          view_key,
          product_id,
          app_name,
          platform,
          group_type,
          entry_type,
          entry_url,
          image_path,
          fallback_url,
          viewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
          legacy_group_id,
          self.service._candidate_product_id(candidate),
          candidate.repo_name,
          Platform.WECOM.value,
          GroupType.UNKNOWN.value,
          'link',
          'https://work.weixin.qq.com/gm/legacy-filter',
          None,
          'https://work.weixin.qq.com/gm/legacy-filter',
          now,
        ),
      )
      connection.commit()

    card = self.service._build_product_card(candidate, [group])
    filtered = self.service._filter_viewed_cards([card])
    self.assertEqual(filtered, [])

  def test_verify_reveals_changed_qrcode_group(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    old_group = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      image_url='https://repo.example.com/qr-old.png',
      image_bytes=make_image_bytes(420, 420),
      image_content_type='image/png',
      entry_url='https://open.feishu.cn/invite/abc',
      fallback_url='https://open.feishu.cn/invite/abc',
      qrcode_verified=True,
      source_urls=['https://repo.example.com/community'],
    )
    new_group = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      image_url='https://repo.example.com/qr-new.png',
      image_bytes=make_image_bytes(460, 460),
      image_content_type='image/png',
      entry_url='https://open.feishu.cn/invite/abc',
      fallback_url='https://open.feishu.cn/invite/abc',
      qrcode_verified=True,
      source_urls=['https://repo.example.com/community'],
    )

    old_card = self.service._build_product_card(candidate, [old_group])
    self.service.mark_group_viewed(old_card.product_id, old_card.app_name, old_card.groups[0])
    new_card = self.service._build_product_card(candidate, [new_group])
    filtered = self.service._filter_viewed_cards([new_card])
    self.assertEqual(len(filtered), 1)
    self.assertEqual(len(filtered[0].groups), 1)

  def test_remove_viewed_group_restores_search_result(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/repo',
      full_name='example/repo',
      repo_name='repo',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.DISCORD,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      entry_url='https://discord.com/invite/repo',
      fallback_url='https://discord.com/invite/repo',
      qrcode_verified=False,
      source_urls=['https://repo.example.com/community'],
    )
    card = self.service._build_product_card(candidate, [group])
    self.service.mark_group_viewed(card.product_id, card.app_name, card.groups[0])
    self.service.remove_viewed_group(card.groups[0].group_id)
    filtered = self.service._filter_viewed_cards([card])
    self.assertEqual(len(filtered), 1)
    self.assertEqual(len(filtered[0].groups), 1)

  def test_mark_group_viewed_exports_qrcode_file(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/qr-tool',
      full_name='example/qr-tool',
      repo_name='qr-tool',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      image_url='https://repo.example.com/qr.png',
      image_bytes=make_image_bytes(420, 420),
      image_content_type='image/png',
      entry_url='https://work.weixin.qq.com/gm/abc',
      fallback_url='https://work.weixin.qq.com/gm/abc',
      qrcode_verified=True,
      source_urls=['https://repo.example.com/community'],
    )

    card = self.service._build_product_card(candidate, [group])
    viewed_group = card.groups[0]
    source_filename = viewed_group.entry.image_path.rsplit('/', 1)[-1]
    source_path = self.service.settings.qrcode_dir / source_filename
    export_filename = self.service._build_viewed_qrcode_export_filename(
      app_name=card.app_name,
      platform=viewed_group.platform,
      view_key=viewed_group.group_id,
      source_filename=source_filename,
    )
    export_path = self.service.settings.viewed_qrcode_dir / export_filename

    try:
      self.service.mark_group_viewed(card.product_id, card.app_name, viewed_group)
      self.assertTrue(export_path.exists())
      self.assertEqual(export_path.read_bytes(), source_path.read_bytes())
    finally:
      if source_path.exists():
        source_path.unlink()
      if export_path.exists():
        export_path.unlink()

  def test_remove_viewed_group_deletes_exported_qrcode_file(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/qr-tool',
      full_name='example/qr-tool',
      repo_name='qr-tool',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      image_url='https://repo.example.com/qr.png',
      image_bytes=make_image_bytes(430, 430),
      image_content_type='image/png',
      entry_url='https://work.weixin.qq.com/gm/remove',
      fallback_url='https://work.weixin.qq.com/gm/remove',
      qrcode_verified=True,
      source_urls=['https://repo.example.com/community'],
    )

    card = self.service._build_product_card(candidate, [group])
    viewed_group = card.groups[0]
    source_filename = viewed_group.entry.image_path.rsplit('/', 1)[-1]
    source_path = self.service.settings.qrcode_dir / source_filename
    export_filename = self.service._build_viewed_qrcode_export_filename(
      app_name=card.app_name,
      platform=viewed_group.platform,
      view_key=viewed_group.group_id,
      source_filename=source_filename,
    )
    export_path = self.service.settings.viewed_qrcode_dir / export_filename

    try:
      self.service.mark_group_viewed(card.product_id, card.app_name, viewed_group)
      self.assertTrue(export_path.exists())
      self.service.remove_viewed_group(viewed_group.group_id)
      self.assertFalse(export_path.exists())
    finally:
      if source_path.exists():
        source_path.unlink()
      if export_path.exists():
        export_path.unlink()

  def test_mark_group_viewed_exports_qq_number_to_csv(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/qq-tool',
      full_name='example/qq-tool',
      repo_name='qq-tool',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.QQ,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      qq_number='123456789',
      qrcode_verified=False,
      source_urls=['https://repo.example.com/community'],
    )

    card = self.service._build_product_card(candidate, [group])
    self.service.mark_group_viewed(card.product_id, card.app_name, card.groups[0])

    rows = self._read_viewed_links_csv()
    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0]['entry_type'], 'qq_number')
    self.assertEqual(rows[0]['entry_value'], '123456789')

  def test_remove_viewed_group_deletes_csv_entry(self):
    view_key = self.service.manual_upload_group(
      app_name='Manual Tool',
      description='manual entry',
      created_at='2026-04-07',
      github_stars=321,
      platform=Platform.DISCORD,
      group_type=GroupType.UNKNOWN,
      entry_type='link',
      entry_url='https://discord.gg/manual-tool',
      fallback_url='https://discord.gg/manual-tool',
      qrcode_bytes=None,
      qrcode_content_type=None,
    )

    self.assertEqual(len(self._read_viewed_links_csv()), 1)
    self.service.remove_viewed_group(view_key)
    self.assertEqual(self._read_viewed_links_csv(), [])

  def test_same_app_multiple_qrcode_exports_do_not_conflict(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/multi-qr-tool',
      full_name='example/multi-qr-tool',
      repo_name='multi-qr-tool',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    first_group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      image_url='https://repo.example.com/qr-1.png',
      image_bytes=make_image_bytes(440, 440),
      image_content_type='image/png',
      entry_url='https://work.weixin.qq.com/gm/1',
      fallback_url='https://work.weixin.qq.com/gm/1',
      qrcode_verified=True,
      source_urls=['https://repo.example.com/community'],
    )
    second_group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      image_url='https://repo.example.com/qr-2.png',
      image_bytes=make_image_bytes(460, 460),
      image_content_type='image/png',
      entry_url='https://work.weixin.qq.com/gm/2',
      fallback_url='https://work.weixin.qq.com/gm/2',
      qrcode_verified=True,
      source_urls=['https://repo.example.com/community'],
    )

    first_card = self.service._build_product_card(candidate, [first_group])
    second_card = self.service._build_product_card(candidate, [second_group])
    first_source = self.service.settings.qrcode_dir / first_card.groups[0].entry.image_path.rsplit('/', 1)[-1]
    second_source = self.service.settings.qrcode_dir / second_card.groups[0].entry.image_path.rsplit('/', 1)[-1]

    try:
      self.service.mark_group_viewed(first_card.product_id, first_card.app_name, first_card.groups[0])
      self.service.mark_group_viewed(second_card.product_id, second_card.app_name, second_card.groups[0])

      exports = sorted(
        export_file.name for export_file in self.service.settings.viewed_qrcode_dir.iterdir() if export_file.is_file()
      )
      self.assertEqual(len(exports), 2)
      self.assertNotEqual(exports[0], exports[1])
    finally:
      if first_source.exists():
        first_source.unlink()
      if second_source.exists():
        second_source.unlink()
      for export_file in self.service.settings.viewed_qrcode_dir.iterdir():
        if export_file.is_file():
          export_file.unlink()

  def test_sync_viewed_exports_skips_missing_qrcode_source(self):
    candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/example/missing-qr-tool',
      full_name='example/missing-qr-tool',
      repo_name='missing-qr-tool',
      owner_name='example',
      owner_type='Organization',
      homepage='https://repo.example.com',
      description='repo',
      stars=10,
    )
    group = ExtractedGroupCandidate(
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      source_url='https://repo.example.com/community',
      context='community',
      image_url='https://repo.example.com/qr-missing.png',
      image_bytes=make_image_bytes(450, 450),
      image_content_type='image/png',
      entry_url='https://work.weixin.qq.com/gm/missing',
      fallback_url='https://work.weixin.qq.com/gm/missing',
      qrcode_verified=True,
      source_urls=['https://repo.example.com/community'],
    )

    card = self.service._build_product_card(candidate, [group])
    viewed_group = card.groups[0]
    source_filename = viewed_group.entry.image_path.rsplit('/', 1)[-1]
    source_path = self.service.settings.qrcode_dir / source_filename
    export_filename = self.service._build_viewed_qrcode_export_filename(
      app_name=card.app_name,
      platform=viewed_group.platform,
      view_key=viewed_group.group_id,
      source_filename=source_filename,
    )
    export_path = self.service.settings.viewed_qrcode_dir / export_filename

    try:
      self.service.mark_group_viewed(card.product_id, card.app_name, viewed_group)
      self.assertTrue(export_path.exists())
      source_path.unlink()

      with patch('app.search.service.logger.warning') as warning_mock:
        self.service._sync_viewed_exports()

      self.assertTrue(export_path.exists())
      warning_mock.assert_any_call(
        'Skipping viewed qrcode export because source file is missing: %s',
        str(source_path),
      )
    finally:
      if source_path.exists():
        source_path.unlink()
      if export_path.exists():
        export_path.unlink()

  def test_github_search_adds_cjk_variants_for_chinese_query(self):
    seen_queries: list[str] = []

    def fake_onevariant(query: str, per_page: int):
      del per_page
      seen_queries.append(query)
      return []

    with patch.object(self.service, '_github_search_onevariant', side_effect=fake_onevariant):
      self.service._github_search('飞书群', limit=10, filters=None)

    self.assertIn('飞书群', seen_queries)
    self.assertIn('飞书群 AI', seen_queries)


  def test_manual_upload_link_is_visible_in_viewed_groups(self):
    view_key = self.service.manual_upload_group(
      app_name='Manual Tool',
      description='manual entry',
      created_at='2026-04-07',
      github_stars=321,
      platform=Platform.WECOM,
      group_type=GroupType.UNKNOWN,
      entry_type='link',
      entry_url='https://work.weixin.qq.com/gm/abc',
      fallback_url='https://work.weixin.qq.com/gm/abc',
      qrcode_bytes=None,
      qrcode_content_type=None,
    )

    viewed = self.service.list_viewed_groups()
    self.assertTrue(any(item.view_key == view_key for item in viewed))
    self.assertTrue(any(item.platform == Platform.WECHAT for item in viewed))
    rows = self._read_viewed_links_csv()
    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0]['view_key'], view_key)
    self.assertEqual(rows[0]['platform'], Platform.WECHAT.value)
    self.assertEqual(rows[0]['entry_type'], 'link')
    self.assertEqual(rows[0]['entry_value'], 'https://work.weixin.qq.com/gm/abc')
    self.assertEqual(rows[0]['fallback_url'], 'https://work.weixin.qq.com/gm/abc')


class EntryExtractorTests(unittest.TestCase):
  def setUp(self):
    self.extractor = EntryExtractor(get_settings())
    self.square_image = make_image_bytes(520, 520)
    self.banner_image = make_image_bytes(900, 420)
    self.small_square_image = make_image_bytes(174, 174)

  def test_extracts_image_link_qrcode_candidate(self):
    page = FetchedPage(
      requested_url='https://fastgpt.io',
      final_url='https://fastgpt.io',
      html='''
        <html><body>
          <a href="https://cdn.fastgpt.io/feishu-group.png">飞书官方群二维码</a>
        </body></html>
      ''',
      title='FastGPT',
      text='',
    )

    with patch.object(
      self.extractor,
      '_download_image',
      return_value=(self.square_image, 'image/png'),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.FEISHU)
    self.assertTrue(candidates[0].image_url.endswith('feishu-group.png'))

  def test_extract_from_link_tag_supports_image_only_wechat_badge(self):
    soup = BeautifulSoup(
      '''
      <a href="https://raw.githubusercontent.com/deepseek-ai/DeepSeek-V2/refs/heads/main/figures/qr.jpeg">
        <img alt="Wechat" src="https://img.shields.io/badge/WeChat-DeepSeek%20AI-brightgreen?logo=wechat" />
      </a>
      ''',
      'html.parser',
    )
    link = soup.find('a')
    self.assertIsNotNone(link)
    assert link is not None
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(
      self.extractor,
      '_download_image',
      return_value=(self.square_image, 'image/jpeg'),
    ), patch.object(
      self.extractor,
      '_analyze_qrcode',
      return_value=('https://u.wechat.com/abcdef', qr_points),
    ):
      candidate = self.extractor._extract_from_link_tag(link, 'https://github.com/deepseek-ai/DeepSeek-V2')

    self.assertIsNotNone(candidate)
    assert candidate is not None
    self.assertEqual(candidate.platform, Platform.WECHAT)
    self.assertTrue(candidate.qrcode_verified)
    self.assertEqual(
      candidate.image_url,
      'https://raw.githubusercontent.com/deepseek-ai/DeepSeek-V2/refs/heads/main/figures/qr.jpeg',
    )

  def test_extract_reuses_cached_page_soup(self):
    html = '''
      <html><body>
        <a href="https://work.weixin.qq.com/gm/abc">Join work wechat group</a>
      </body></html>
    '''
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html=html,
      title='Community',
      text='',
      soup=BeautifulSoup(html, 'html.parser'),
    )

    with patch('app.search.entry_extractor.BeautifulSoup', side_effect=AssertionError('should reuse cached soup')):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.WECOM)

  def test_extract_non_github_page_scans_footer_qrcode(self):
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html='''
        <html><body>
          <article>
            <img src="https://cdn.example.com/hero.png" alt="hero banner" />
          </article>
          <footer>
            <img src="https://cdn.example.com/footer-wechat-qrcode.png" alt="wechat discussion group qrcode" />
          </footer>
        </body></html>
      ''',
      title='Example',
      text='',
    )
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(
      self.extractor,
      '_download_image',
      return_value=(self.square_image, 'image/png'),
    ), patch.object(
      self.extractor,
      '_analyze_qrcode',
      return_value=(None, qr_points),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.WECHAT)
    self.assertTrue(candidates[0].image_url.endswith('footer-wechat-qrcode.png'))
    self.assertEqual(candidates[0].source_url, 'https://example.com')

  def test_extract_github_page_keeps_readme_scoping(self):
    page = FetchedPage(
      requested_url='https://github.com/example/repo',
      final_url='https://github.com/example/repo',
      html='''
        <html><body>
          <article class="markdown-body">
            <a href="https://discord.com/invite/readme-main">Discord community</a>
          </article>
          <footer>
            <a href="https://discord.com/invite/footer-noise">Discord footer community</a>
          </footer>
        </body></html>
      ''',
      title='Repo',
      text='',
    )

    candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.DISCORD)
    self.assertEqual(candidates[0].entry_url, 'https://discord.com/invite/readme-main')

  def test_extract_visual_candidates_reuse_download_for_same_image_url(self):
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html='''
        <html><body>
          <img src="https://cdn.example.com/shared-qr.png" alt="Wechat community qrcode" />
          <a href="https://cdn.example.com/shared-qr.png">Discord community QR</a>
        </body></html>
      ''',
      title='Example',
      text='',
    )
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(
      self.extractor,
      '_download_image',
      return_value=(self.square_image, 'image/png'),
    ) as download_mock, patch.object(
      self.extractor,
      '_analyze_qrcode',
      return_value=(None, qr_points),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(download_mock.call_count, 1)
    self.assertEqual(len(candidates), 2)
    self.assertEqual([candidate.platform for candidate in candidates], [Platform.WECHAT, Platform.DISCORD])

  def test_extract_visual_candidates_preserve_original_task_order(self):
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html='''
        <html><body>
          <img src="https://cdn.example.com/wechat.png" alt="Wechat community qrcode" />
          <img src="https://cdn.example.com/discord.png" alt="Discord community qrcode" />
        </body></html>
      ''',
      title='Example',
      text='',
    )
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    def fake_download(image_url: str):
      if image_url.endswith('wechat.png'):
        time.sleep(0.05)
      return self.square_image, 'image/png'

    with patch.object(self.extractor, '_download_image', side_effect=fake_download), patch.object(
      self.extractor,
      '_analyze_qrcode',
      return_value=(None, qr_points),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(
      [candidate.image_url for candidate in candidates],
      ['https://cdn.example.com/wechat.png', 'https://cdn.example.com/discord.png'],
    )

  def test_filters_chat_screenshot_before_download(self):
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html='''
        <html><body>
          <img src="https://cdn.example.com/wechat-chat.png" alt="微信聊天截图" />
        </body></html>
      ''',
      title='Example',
      text='',
    )

    with patch.object(self.extractor, '_download_image') as download_mock:
      candidates = self.extractor.extract([page])

    self.assertEqual(candidates, [])
    download_mock.assert_not_called()

  def test_crops_large_image_when_qr_points_detected(self):
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html='''
        <html><body>
          <img src="https://cdn.example.com/discord-group-banner.png" alt="Discord community QR" />
        </body></html>
      ''',
      title='Example',
      text='',
    )
    qr_points = np.array([[[520.0, 120.0], [650.0, 120.0], [650.0, 250.0], [520.0, 250.0]]], dtype=np.float32)

    with patch.object(
      self.extractor,
      '_download_image',
      return_value=(self.banner_image, 'image/png'),
    ), patch.object(
      self.extractor,
      '_analyze_qrcode',
      return_value=(None, qr_points),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    cropped = cv2.imdecode(np.frombuffer(candidates[0].image_bytes or b'', dtype=np.uint8), cv2.IMREAD_COLOR)
    self.assertIsNotNone(cropped)
    self.assertLess(cropped.shape[1], 200)
    self.assertLess(cropped.shape[0], 200)

  def test_rejects_chat_screenshot_when_qr_detected_but_uncropped(self):
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(self.extractor, '_analyze_qrcode', return_value=(None, qr_points)), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=None,
    ):
      candidate = self.extractor._extract_visual_candidate(
        image_url='https://cdn.example.com/dingtalk-chat.png',
        page_url='https://example.com',
        full_context='dingtalk group chat history screenshot',
        image_bytes=self.banner_image,
        content_type='image/png',
        entry_url=None,
      )

    self.assertIsNone(candidate)

  def test_rejects_document_screenshot_in_fallback_path(self):
    with patch.object(self.extractor, '_analyze_qrcode', return_value=(None, None)):
      candidate = self.extractor._extract_visual_candidate(
        image_url='https://cdn.example.com/wecom-doc.png',
        page_url='https://example.com',
        full_context='work wechat group document instruction',
        image_bytes=self.square_image,
        content_type='image/png',
        entry_url=None,
      )

    self.assertIsNone(candidate)

  def test_collect_nearby_text_avoids_cross_image_parent_text(self):
    soup = BeautifulSoup(
      '''
      <div>
        <img src="https://cdn.example.com/wechat-account.png" alt="wechat official account qrcode" />
        <img src="https://cdn.example.com/wechat-group.png" alt="wechat discussion group qrcode" />
      </div>
      ''',
      'html.parser',
    )
    images = soup.find_all('img')
    nearby = self.extractor._collect_nearby_text(images[0])

    self.assertIn('official account', nearby.lower())
    self.assertNotIn('discussion group', nearby.lower())

  def test_strong_group_intent_includes_wechat_and_feishu_group_terms(self):
    self.assertTrue(self.extractor._has_strong_group_intent('\u5fae\u4fe1\u7fa4\u4e8c\u7ef4\u7801'))
    self.assertTrue(self.extractor._has_strong_group_intent('\u98de\u4e66\u7fa4\u4e8c\u7ef4\u7801'))

  def test_collect_nearby_text_uses_adjacent_text_for_multi_image_parent(self):
    soup = BeautifulSoup(
      '''
      <div>
        <img src="https://cdn.example.com/image-a.png" />
        <span>wechat official account qrcode</span>
        <img src="https://cdn.example.com/image-b.png" />
        <span>wechat discussion group qrcode</span>
      </div>
      ''',
      'html.parser',
    )
    images = soup.find_all('img')
    first_nearby = self.extractor._collect_nearby_text(images[0]).lower()
    second_nearby = self.extractor._collect_nearby_text(images[1]).lower()

    self.assertIn('official account', first_nearby)
    self.assertNotIn('discussion group', first_nearby)
    self.assertIn('discussion group', second_nearby)
    self.assertNotIn('official account', second_nearby)

  def test_allows_undecoded_qr_with_wechat_group_strong_intent(self):
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(self.extractor, '_analyze_qrcode', return_value=(None, qr_points)), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=self.small_square_image,
    ):
      candidate = self.extractor._extract_visual_candidate(
        image_url='https://cdn.example.com/static/hash-image',
        page_url='https://example.com',
        full_context='\u5fae\u4fe1\u7fa4\u4e8c\u7ef4\u7801',
        image_bytes=self.square_image,
        content_type='image/png',
        entry_url=None,
      )

    self.assertIsNotNone(candidate)
    assert candidate is not None
    self.assertEqual(candidate.platform, Platform.WECHAT)

  def test_rejects_official_account_visual_candidate_without_group_intent(self):
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(self.extractor, '_analyze_qrcode', return_value=(None, qr_points)), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=self.small_square_image,
    ):
      candidate = self.extractor._extract_visual_candidate(
        image_url='https://cdn.example.com/image-a.png',
        page_url='https://example.com',
        full_context='wechat official account qrcode',
        image_bytes=self.square_image,
        content_type='image/png',
        entry_url=None,
      )

    self.assertIsNone(candidate)

  def test_prefers_discussion_group_qrcode_over_account_qrcode(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html='''
        <html><body>
          <div>
            <img src="https://cdn.example.com/wechat-account-qrcode.png" alt="wechat official account qrcode" />
            <img src="https://cdn.example.com/wechat-discussion-group-qrcode.png" alt="wechat discussion group qrcode" />
          </div>
        </body></html>
      ''',
      title='community',
      text='',
    )
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(
      self.extractor,
      '_download_image',
      return_value=(self.square_image, 'image/png'),
    ), patch.object(
      self.extractor,
      '_analyze_qrcode',
      return_value=(None, qr_points),
    ), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=self.small_square_image,
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertIn('discussion-group', candidates[0].image_url)

  def test_extract_multi_qrcode_container_prefers_group_and_filters_account(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html='''
        <html><body>
          <footer>
            <div>
              <img src="https://cdn.example.com/image-a.png" />
              <span>wechat official account qrcode</span>
              <img src="https://cdn.example.com/image-b.png" />
              <span>wechat discussion group qrcode</span>
            </div>
          </footer>
        </body></html>
      ''',
      title='community',
      text='',
    )
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(
      self.extractor,
      '_download_image',
      return_value=(self.square_image, 'image/png'),
    ), patch.object(
      self.extractor,
      '_analyze_qrcode',
      return_value=(None, qr_points),
    ), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=self.small_square_image,
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.WECHAT)
    self.assertTrue(candidates[0].image_url.endswith('image-b.png'))

  def test_allows_undecoded_qr_with_qr_url_hint(self):
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)
    with patch.object(self.extractor, '_analyze_qrcode', return_value=(None, qr_points)), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=self.square_image,
    ):
      candidate = self.extractor._extract_visual_candidate(
        image_url='https://cdn.example.com/discord-qrcode.png',
        page_url='https://example.com',
        full_context='discord community group',
        image_bytes=self.square_image,
        content_type='image/png',
        entry_url=None,
      )

    self.assertIsNotNone(candidate)
    self.assertEqual(candidate.platform, Platform.DISCORD)
    self.assertTrue(candidate.qrcode_verified)

  def test_promotes_small_undecoded_qr_to_qrcode_when_high_confidence(self):
    qr_points = np.array([[[24.0, 24.0], [152.0, 24.0], [152.0, 152.0], [24.0, 152.0]]], dtype=np.float32)
    cropped = make_image_bytes(120, 120)

    with patch.object(self.extractor, '_analyze_qrcode', return_value=(None, qr_points)), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=cropped,
    ):
      candidate = self.extractor._extract_visual_candidate(
        image_url='https://camo.githubusercontent.com/hash/68747470733a2f2f6578616d706c652e636f6d2f71722e706e67',
        page_url='https://github.com/coze-dev/coze-studio',
        full_context='feishu official group qrcode invite',
        image_bytes=self.small_square_image,
        content_type='image/png',
        entry_url='https://open.feishu.cn/invite/community-abc',
      )

    self.assertIsNotNone(candidate)
    self.assertTrue(candidate.qrcode_verified)
    self.assertEqual(candidate.entry_url, 'https://open.feishu.cn/invite/community-abc')

  def test_promotes_undecoded_qr_without_link_when_high_confidence(self):
    qr_points = np.array([[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]], dtype=np.float32)

    with patch.object(self.extractor, '_analyze_qrcode', return_value=(None, qr_points)), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=self.small_square_image,
    ):
      candidate = self.extractor._extract_visual_candidate(
        image_url='https://oss.laf.run/otnvvf-imgs/fastgpt-feishu1.png',
        page_url='https://fastgpt.io',
        full_context='feishu official discussion group join community',
        image_bytes=self.square_image,
        content_type='image/png',
        entry_url=None,
      )

    self.assertIsNotNone(candidate)
    self.assertTrue(candidate.qrcode_verified)
    self.assertIsNone(candidate.entry_url)

  def test_small_undecoded_qr_without_reliable_link_is_not_promoted(self):
    qr_points = np.array([[[24.0, 24.0], [152.0, 24.0], [152.0, 152.0], [24.0, 152.0]]], dtype=np.float32)

    with patch.object(self.extractor, '_analyze_qrcode', return_value=(None, qr_points)), patch.object(
      self.extractor,
      '_crop_qrcode',
      return_value=self.small_square_image,
    ):
      candidate = self.extractor._extract_visual_candidate(
        image_url='https://cdn.example.com/discord-qrcode.png',
        page_url='https://example.com',
        full_context='discord qrcode',
        image_bytes=self.small_square_image,
        content_type='image/png',
        entry_url=None,
      )

    self.assertIsNotNone(candidate)
    self.assertFalse(candidate.qrcode_verified)

  def test_allows_discord_link_entry(self):
    page = FetchedPage(
      requested_url='https://lovable.dev',
      final_url='https://lovable.dev',
      html='''
        <html><body>
          <a href="https://discord.com/invite/lovable-dev">Community Discord Server</a>
        </body></html>
      ''',
      title='Lovable',
      text='',
    )

    candidates = self.extractor.extract([page])
    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.DISCORD)
    self.assertEqual(candidates[0].entry_url, 'https://discord.com/invite/lovable-dev')

  def test_extract_prioritizes_qr_like_images_on_busy_page(self):
    page = FetchedPage(
      requested_url='https://github.com/org/repo',
      final_url='https://github.com/org/repo',
      html='''
        <html><body>
          <img src="https://cdn.example.com/badge-1.png" alt="build badge" />
          <img src="https://cdn.example.com/badge-2.png" alt="coverage badge" />
          <img src="https://cdn.example.com/banner.png" alt="hero image" />
          <img src="https://cdn.example.com/discord-qrcode.png" alt="Discord community QR" />
        </body></html>
      ''',
      title='Repo',
      text='',
    )

    with patch.object(
      self.extractor,
      '_download_image',
      return_value=(self.square_image, 'image/png'),
    ):
      candidates = self.extractor.extract([page])

    self.assertTrue(any(candidate.platform == Platform.DISCORD for candidate in candidates))

  def test_allows_wecom_and_dingtalk_link_entries(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html='''
        <html><body>
          <a href="https://work.weixin.qq.com/gm/abc">Join work wechat group</a>
          <a href="https://qr.dingtalk.com/action/join?code=123">Join dingtalk community</a>
        </body></html>
      ''',
      title='Community',
      text='',
    )

    candidates = self.extractor.extract([page])
    platforms = {candidate.platform for candidate in candidates}
    self.assertIn(Platform.WECOM, platforms)
    self.assertIn(Platform.DINGTALK, platforms)

  def test_filters_discord_non_invite_links(self):
    page = FetchedPage(
      requested_url='https://n8n.io',
      final_url='https://n8n.io',
      html='''
        <html><body>
          <a href="https://n8n.io/workflows/2105-get-all-members-of-a-discord-server-with-a-specific-role/">
            Discord integration workflow
          </a>
        </body></html>
      ''',
      title='n8n',
      text='',
    )

    candidates = self.extractor.extract([page])
    self.assertEqual(candidates, [])

  def test_filters_download_and_article_links(self):
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html='''
        <html><body>
          <a href="https://dldir1.qq.com/wework/work_weixin/WeCom_4.1.13.6002.exe">Join work wechat group</a>
          <a href="https://mp.weixin.qq.com/s/abcdef">QQ group community</a>
        </body></html>
      ''',
      title='Example',
      text='',
    )

    candidates = self.extractor.extract([page])
    self.assertEqual(candidates, [])

  def test_filters_payment_links(self):
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html='''
        <html><body>
          <a href="https://pay.weixin.qq.com/pay">Join WeChat Group</a>
        </body></html>
      ''',
      title='Example',
      text='',
    )

    candidates = self.extractor.extract([page])
    self.assertEqual(candidates, [])

  def test_filters_external_store_links_even_with_group_words(self):
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html='''
        <html><body>
          <a href="https://www.amazon.com/dp/B0DTH4M7HT">Join QQ group now</a>
        </body></html>
      ''',
      title='Example',
      text='',
    )

    candidates = self.extractor.extract([page])
    self.assertEqual(candidates, [])

  def test_analyze_qrcode_multiscale_window_fallback_hits(self):
    large = np.full((1600, 1600, 3), 255, dtype=np.uint8)
    calls: list[tuple[int, int]] = []

    def fake_detect(_detector, image):
      h, w = image.shape[:2]
      calls.append((w, h))
      if (w, h) == (640, 640):
        points = np.array(
          [[[80.0, 80.0], [220.0, 80.0], [220.0, 220.0], [80.0, 220.0]]],
          dtype=np.float32,
        )
        return None, points
      return None, None

    with patch.object(self.extractor, '_detect_qrcode_once', side_effect=fake_detect):
      payload, points = self.extractor._analyze_qrcode(large)

    self.assertIsNone(payload)
    self.assertIsNotNone(points)
    self.assertGreater(len(calls), 1)

  def test_analyze_qrcode_small_image_retries_with_white_border(self):
    small = np.full((174, 174, 3), 255, dtype=np.uint8)
    calls: list[tuple[int, int]] = []

    def fake_detect(_detector, image):
      h, w = image.shape[:2]
      calls.append((w, h))
      if (w, h) in {(214, 214), (254, 254)}:
        points = np.array(
          [[[40.0, 40.0], [140.0, 40.0], [140.0, 140.0], [40.0, 140.0]]],
          dtype=np.float32,
        )
        return None, points
      return None, None

    with patch.object(self.extractor, '_detect_qrcode_once', side_effect=fake_detect):
      payload, points = self.extractor._analyze_qrcode(small)

    self.assertIsNone(payload)
    self.assertIsNotNone(points)
    self.assertIn((214, 214), calls)

  def test_analyze_qrcode_medium_image_retries_with_white_border(self):
    medium = np.full((530, 530, 3), 255, dtype=np.uint8)
    calls: list[tuple[int, int]] = []

    def fake_detect(_detector, image):
      h, w = image.shape[:2]
      calls.append((w, h))
      if (w, h) == (610, 610):
        points = np.array(
          [[[80.0, 80.0], [260.0, 80.0], [260.0, 260.0], [80.0, 260.0]]],
          dtype=np.float32,
        )
        return 'https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=test', points
      return None, None

    with patch.object(self.extractor, '_detect_qrcode_once', side_effect=fake_detect):
      payload, points = self.extractor._analyze_qrcode(medium)

    self.assertIsNotNone(payload)
    self.assertIsNotNone(points)
    self.assertIn((610, 610), calls)

  def test_analyze_qrcode_retries_with_preprocess_upsampling(self):
    sample = np.full((320, 320, 3), 255, dtype=np.uint8)
    calls: list[tuple[int, int]] = []

    def fake_detect(_detector, image):
      h, w = image.shape[:2]
      calls.append((w, h))
      if (w, h) == (480, 480):
        points = np.array(
          [[[120.0, 120.0], [360.0, 120.0], [360.0, 360.0], [120.0, 360.0]]],
          dtype=np.float32,
        )
        return None, points
      return None, None

    with patch.object(self.extractor, '_detect_qrcode_once', side_effect=fake_detect):
      payload, points = self.extractor._analyze_qrcode(sample)

    self.assertIsNone(payload)
    self.assertIsNotNone(points)
    self.assertIn((480, 480), calls)
    mapped = np.squeeze(points)
    self.assertLessEqual(float(mapped.max()), 320.0)

  def test_extracts_qq_group_numbers_from_group_context(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html='''
        <html><body>
          <p>问题与建议：当然也可以加入我们新建的QQ群讨论：549762872, 205872856</p>
        </body></html>
      ''',
      title='community',
      text='',
    )

    candidates = self.extractor.extract([page])
    qq_numbers = sorted(candidate.qq_number for candidate in candidates if candidate.platform == Platform.QQ and candidate.qq_number)
    self.assertEqual(qq_numbers, ['205872856', '549762872'])

  def test_extracts_qq_group_numbers_from_multiline_context(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html='''
        <html><body>
          <p>问题与建议：当然也可以加入我们新建的QQ群讨论：</p>
          <p>549762872, 205872856</p>
        </body></html>
      ''',
      title='community',
      text='',
    )

    candidates = self.extractor.extract([page])
    qq_numbers = sorted(candidate.qq_number for candidate in candidates if candidate.platform == Platform.QQ and candidate.qq_number)
    self.assertEqual(qq_numbers, ['205872856', '549762872'])

  def test_does_not_extract_qq_number_with_account_context_across_lines(self):
    page = FetchedPage(
      requested_url='https://example.com/contact',
      final_url='https://example.com/contact',
      html='''
        <html><body>
          <p>如需帮助请联系 QQ号：</p>
          <p>549762872</p>
        </body></html>
      ''',
      title='contact',
      text='',
    )

    candidates = self.extractor.extract([page])
    self.assertFalse(any(candidate.qq_number for candidate in candidates))

  def test_does_not_extract_plain_numbers_without_qq_group_context(self):
    page = FetchedPage(
      requested_url='https://example.com/changelog',
      final_url='https://example.com/changelog',
      html='''
        <html><body>
          <p>Version 2026.04.07 build 549762872 fixed issues.</p>
        </body></html>
      ''',
      title='changelog',
      text='',
    )

    candidates = self.extractor.extract([page])
    self.assertFalse(any(candidate.qq_number for candidate in candidates))


class SearchApiTests(unittest.TestCase):
  def setUp(self):
    self.client = TestClient(app)

  def _test_search_returns_empty_message_when_no_results_legacy(self):
    with patch('app.api.routes.search_service.search', return_value=[]):
      response = self.client.post('/api/search', json={'query': 'some_nonexistent_product_xyz'})

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()['empty_message'], '未在 GitHub 相关仓库中发现群聊二维码')

  def test_search_returns_empty_message_when_no_results(self):
    with patch('app.api.routes.search_service.search', return_value=[]):
      response = self.client.post('/api/search', json={'query': 'some_nonexistent_product_xyz'})

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()['empty_message'], '未在 GitHub/官网相关页面中发现官方群入口')

  def test_search_returns_cards_when_results_found(self):
    mock_card = ProductCard(
      product_id='abc123',
      app_name='TestApp',
      description='A test app',
      github_stars=100,
      created_at=None,
      verified_at=datetime.now(timezone.utc),
      groups=[],
      group_discovery_status=GroupDiscoveryStatus.NOT_FOUND,
      official_site_url='https://testapp.dev',
      github_repo_url='https://github.com/example/testapp',
    )
    with patch('app.api.routes.search_service.search', return_value=[mock_card]):
      response = self.client.post('/api/search', json={'query': 'testapp'})

    self.assertEqual(response.status_code, 200)
    self.assertEqual(len(response.json()['results']), 1)
    self.assertIsNone(response.json()['empty_message'])

  def test_search_response_keeps_all_groups(self):
    mock_card = ProductCard(
      product_id='abc123',
      app_name='TestApp',
      description='A test app',
      github_stars=100,
      created_at=None,
      verified_at=datetime.now(timezone.utc),
      groups=[
        {
          'group_id': 'group-discord',
          'platform': Platform.DISCORD,
          'group_type': GroupType.UNKNOWN,
          'entry': {'type': 'link', 'url': 'https://discord.com/invite/testapp'},
          'is_added': False,
          'source_urls': ['https://example.com/community'],
        },
        {
          'group_id': 'group-qq',
          'platform': Platform.QQ,
          'group_type': GroupType.UNKNOWN,
          'entry': {'type': 'qq_number', 'qq_number': '123456789'},
          'is_added': False,
          'source_urls': ['https://example.com/community'],
        },
      ],
      group_discovery_status=GroupDiscoveryStatus.FOUND,
      official_site_url='https://testapp.dev',
      github_repo_url='https://github.com/example/testapp',
    )
    with patch('app.api.routes.search_service.search', return_value=[mock_card]):
      response = self.client.post('/api/search', json={'query': 'testapp'})

    self.assertEqual(response.status_code, 200)
    payload = response.json()
    self.assertEqual(len(payload['results']), 1)
    self.assertEqual(len(payload['results'][0]['groups']), 2)

  def test_search_refresh_flag_forces_refresh(self):
    with patch('app.api.routes.search_service.search', return_value=[]) as search_mock:
      response = self.client.post('/api/search', json={'query': 'n8n', 'refresh': True})

    self.assertEqual(response.status_code, 200)
    self.assertTrue(search_mock.call_args.kwargs.get('refresh'))

  def test_recommendations_refresh_flag_forces_refresh(self):
    payload = RecommendationsResponse(
      tools=[RecommendedTool(name='FastGPT', full_name='labring/FastGPT', stars=1, description=None, topics=[])],
      cached_at=datetime.now(timezone.utc),
    )
    with patch('app.api.routes.search_service.get_recommendations', return_value=payload) as get_recommendations_mock:
      response = self.client.get('/api/recommendations?refresh=true')

    self.assertEqual(response.status_code, 200)
    get_recommendations_mock.assert_called_once_with(force_refresh=True)

  def test_mark_group_viewed_endpoint(self):
    payload = {
      'product_id': 'abc123',
      'app_name': 'TestApp',
      'group': {
        'group_id': 'group001',
        'platform': Platform.DISCORD.value,
        'group_type': GroupType.UNKNOWN.value,
        'entry': {'type': 'link', 'url': 'https://discord.com/invite/test'},
        'is_added': False,
        'source_urls': ['https://example.com'],
      },
    }
    with patch('app.api.routes.search_service.mark_group_viewed') as mark_mock:
      response = self.client.post('/api/groups/viewed', json=payload)

    self.assertEqual(response.status_code, 200)
    mark_mock.assert_called_once()

  def test_list_viewed_groups_endpoint(self):
    with patch('app.api.routes.search_service.list_viewed_groups', return_value=[]):
      response = self.client.get('/api/groups/viewed')

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()['groups'], [])

  def test_remove_viewed_group_endpoint(self):
    with patch('app.api.routes.search_service.remove_viewed_group') as remove_mock:
      response = self.client.delete('/api/groups/viewed/group001')

    self.assertEqual(response.status_code, 200)
    remove_mock.assert_called_once_with('group001')

  def test_search_forwards_limit_parameter(self):
    with patch('app.api.routes.search_service.search', return_value=[]) as search_mock:
      response = self.client.post('/api/search', json={'query': 'n8n', 'limit': 50})

    self.assertEqual(response.status_code, 200)
    self.assertEqual(search_mock.call_args.kwargs.get('limit'), 50)

  def test_search_rejects_limit_above_hard_limit(self):
    with patch('app.api.routes.search_service.search', return_value=[]) as search_mock:
      response = self.client.post('/api/search', json={'query': 'n8n', 'limit': 51})

    self.assertEqual(response.status_code, 422)
    search_mock.assert_not_called()

  def test_manual_upload_link_mode_endpoint(self):
    with patch('app.api.routes.search_service.manual_upload_group', return_value='manual001') as upload_mock:
      response = self.client.post(
        '/api/groups/manual-upload',
        data={
          'app_name': 'Manual Tool',
          'platform': Platform.DINGTALK.value,
          'group_type': GroupType.UNKNOWN.value,
          'entry_type': 'link',
          'entry_url': 'https://qr.dingtalk.com/action/join?code=123',
        },
      )

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()['view_key'], 'manual001')
    upload_mock.assert_called_once()

  def test_manual_upload_qrcode_mode_requires_file(self):
    response = self.client.post(
      '/api/groups/manual-upload',
      data={
        'app_name': 'Manual Tool',
        'platform': Platform.DISCORD.value,
        'group_type': GroupType.UNKNOWN.value,
        'entry_type': 'qrcode',
      },
    )

    self.assertEqual(response.status_code, 400)

  def test_manual_upload_link_mode_requires_entry_url(self):
    response = self.client.post(
      '/api/groups/manual-upload',
      data={
        'app_name': 'Manual Tool',
        'platform': Platform.WECOM.value,
        'group_type': GroupType.UNKNOWN.value,
        'entry_type': 'link',
      },
    )

    self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
  unittest.main()
