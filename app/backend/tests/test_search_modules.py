import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.schemas import GroupType, Platform
from app.core.config import get_settings
from app.search.entry_extractor import EntryExtractor
from app.search.group_type_classifier import GroupTypeClassifier
from app.search.models import (
  CandidatePageSummary,
  DiscoveredTargets,
  ExtractedGroupCandidate,
  ExtractionStats,
  FetchedPage,
  GitHubCandidateSummary,
  GitHubRepositoryCandidate,
  GitHubRepositoryMetadata,
  SearchResultLink,
  SearchTrace,
)
from app.search.official_source_validator import OfficialSourceValidator
from app.search.page_fetcher import PageFetcher
from app.search.result_normalizer import ResultNormalizer
from app.search.search_entry import SearchEntry
from app.search.service import SearchService
from main import app


class SearchEntryTests(unittest.TestCase):
  def test_normalize_domain_query(self):
    normalized = SearchEntry().normalize(' https://cursor.com/ ')

    self.assertEqual(normalized.query_type, 'domain')
    self.assertEqual(normalized.domain, 'cursor.com')

  def test_normalize_keyword_query(self):
    normalized = SearchEntry().normalize('  Cursor   AI  ')

    self.assertEqual(normalized.query_type, 'keyword')
    self.assertEqual(normalized.cleaned_query, 'Cursor AI')

  def test_normalize_github_repo_url(self):
    normalized = SearchEntry().normalize('https://github.com/labring/FastGPT')

    self.assertEqual(normalized.query_type, 'github_repo')
    self.assertEqual(normalized.cleaned_query, 'FastGPT')
    self.assertEqual(normalized.explicit_repo_url, 'https://github.com/labring/FastGPT')


class GroupTypeClassifierTests(unittest.TestCase):
  def test_classify_support_context(self):
    classifier = GroupTypeClassifier()

    self.assertEqual(
      classifier.classify('加入官方群答疑支持 support'),
      GroupType.QA,
    )


class OfficialSourceValidatorTests(unittest.TestCase):
  def test_official_url_checks(self):
    validator = OfficialSourceValidator()

    self.assertTrue(
      validator.is_official_url(
        'https://cursor.com/community',
        'https://cursor.com',
        None,
      ),
    )
    self.assertTrue(
      validator.is_official_url(
        'https://github.com/getcursor/cursor/blob/main/README.md',
        None,
        'https://github.com/getcursor/cursor',
      ),
    )
    self.assertFalse(
      validator.is_official_url(
        'https://discord.gg/example',
        'https://cursor.com',
        'https://github.com/getcursor/cursor',
      ),
    )


class PageFetcherTests(unittest.TestCase):
  def setUp(self):
    self.fetcher = PageFetcher(get_settings())

  def test_discover_candidate_internal_links_scores_strong_and_weak_signals(self):
    html = """
      <main>
        <nav>
          <a href="/community">Community</a>
          <a href="/support">Support</a>
        </nav>
        <section class="news-list">
          <a href="/news/minimax-community">MiniMax 社区活动</a>
          <a href="/blog/product-update">Blog update</a>
        </section>
        <a href="https://external.example.com/community">External</a>
      </main>
    """
    page = FetchedPage(
      requested_url='https://example.com',
      final_url='https://example.com',
      html=html,
      title='home',
      text='links',
    )

    candidates = self.fetcher.discover_candidate_internal_links(page, limit=8)
    urls = [candidate.url for candidate in candidates]

    self.assertIn('https://example.com/community', urls)
    self.assertIn('https://example.com/support', urls)
    self.assertIn('https://example.com/news/minimax-community', urls)
    self.assertNotIn('https://external.example.com/community', urls)
    self.assertLessEqual(len(candidates), 8)


class EntryExtractorTests(unittest.TestCase):
  def setUp(self):
    self.extractor = EntryExtractor(get_settings())

  def test_extracts_qrcode_candidate_when_decode_succeeds(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html=(
        '<section><p>加入飞书群获取最新消息</p>'
        '<a href="https://www.feishu.cn/invite/abc">'
        '<img src="/qr.png" alt="飞书群二维码" /></a></section>'
      ),
      title='community',
      text='加入飞书群获取最新消息',
    )

    with (
      patch.object(self.extractor, '_download_image', return_value=(b'png', 'image/png')),
      patch.object(
        self.extractor,
        '_decode_qrcode',
        return_value=('https://www.feishu.cn/invite/abc', True),
      ),
      patch.object(self.extractor, '_store_image', return_value='/assets/qrcodes/mock.png'),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.FEISHU)
    self.assertEqual(candidates[0].group_type, GroupType.DISCUSSION)
    self.assertEqual(candidates[0].image_url, '/assets/qrcodes/mock.png')
    self.assertTrue(candidates[0].qrcode_verified)

  def test_keeps_unverified_qrcode_image_when_context_is_strong(self):
    page = FetchedPage(
      requested_url='https://github.com/labring/FastGPT',
      final_url='https://github.com/labring/FastGPT',
      html=(
        '<section><h2>社区交流群</h2><p>加入飞书群</p>'
        '<img data-canonical-src="https://oss.example.com/fastgpt-feishu-qr.png" /></section>'
      ),
      title='FastGPT',
      text='社区交流群 加入飞书群',
    )

    with (
      patch.object(self.extractor, '_download_image', return_value=(b'png', 'image/png')),
      patch.object(self.extractor, '_decode_qrcode', return_value=(None, False)),
      patch.object(self.extractor, '_looks_like_qrcode_image', return_value=True),
      patch.object(self.extractor, '_store_image', return_value='/assets/qrcodes/fastgpt.png'),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.FEISHU)
    self.assertEqual(candidates[0].image_url, '/assets/qrcodes/fastgpt.png')
    self.assertFalse(candidates[0].qrcode_verified)

  def test_filters_github_anchor_noise(self):
    page = FetchedPage(
      requested_url='https://github.com/labring/FastGPT',
      final_url='https://github.com/labring/FastGPT',
      html=(
        '<section><h2>社区交流群</h2>'
        '<a class="anchor" aria-label="Permalink: 社区交流群" href="#社区交流群">#</a>'
        '<a href="https://fael3z0zfze.feishu.cn/share/base/form/abc">飞书咨询</a>'
        '</section>'
      ),
      title='FastGPT',
      text='社区交流群 飞书咨询',
    )
    stats = ExtractionStats()

    candidates = self.extractor.extract([page], stats)

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].entry_url, 'https://fael3z0zfze.feishu.cn/share/base/form/abc')
    self.assertGreaterEqual(stats.filtered_link_noise, 1)

  def test_extract_stats_capture_fallback_counts(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html='<section><p>加入微信群 扫码入群</p><img src="/wechat-qr.png" /></section>',
      title='community',
      text='加入微信群 扫码入群',
    )
    stats = ExtractionStats()

    with (
      patch.object(self.extractor, '_download_image', return_value=(b'png', 'image/png')),
      patch.object(self.extractor, '_decode_qrcode', return_value=(None, False)),
      patch.object(self.extractor, '_looks_like_qrcode_image', return_value=True),
      patch.object(self.extractor, '_store_image', return_value='/assets/qrcodes/wechat.png'),
    ):
      candidates = self.extractor.extract([page], stats)

    self.assertEqual(len(candidates), 1)
    self.assertEqual(stats.image_candidates, 1)
    self.assertEqual(stats.image_decode_fallbacks, 1)
    self.assertEqual(stats.output_candidates, 1)
    self.assertEqual(stats.page_summaries[0].image_candidates, 1)


class ResultNormalizerTests(unittest.TestCase):
  def setUp(self):
    self.normalizer = ResultNormalizer()

  def test_builds_link_entry_card(self):
    groups = [
      ExtractedGroupCandidate(
        platform=Platform.WECHAT,
        group_type=GroupType.DISCUSSION,
        source_url='https://example.com/community',
        context='加入微信群交流',
        entry_url='https://example.com/invite',
      ),
    ]
    github = GitHubRepositoryMetadata(
      repo_url='https://github.com/example/app',
      stars=123,
      created_at='2025-01-02T03:04:05Z',
      description='Example app',
    )

    cards = self.normalizer.build_product_card(
      app_name='Example App',
      description='Example app',
      github=github,
      groups=groups,
    )

    self.assertEqual(len(cards), 1)
    self.assertEqual(cards[0].github_stars, 123)
    self.assertEqual(cards[0].groups[0].entry.type, 'link')

  def test_returns_empty_list_when_no_groups(self):
    cards = self.normalizer.build_product_card(
      app_name='Example App',
      description='Example app',
      github=None,
      groups=[],
    )

    self.assertEqual(cards, [])


class SearchServiceTests(unittest.TestCase):
  def setUp(self):
    self.service = SearchService(get_settings())

  def test_resolve_search_result_url_decodes_bing_redirect(self):
    resolved = self.service._resolve_search_result_url(
      'https://www.bing.com/ck/a?!&&p=test&u=a1aHR0cHM6Ly9jdXN0b20tY3Vyc29yLmNvbS8&ntb=1',
    )

    self.assertEqual(resolved, 'https://custom-cursor.com/')

  def test_search_bing_parses_rss_items(self):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.text = """
      <rss>
        <channel>
          <item>
            <title>Claude</title>
            <link>https://claude.ai/</link>
          </item>
          <item>
            <title>Home \\ Anthropic</title>
            <link>https://www.anthropic.com/</link>
          </item>
        </channel>
      </rss>
    """

    client = MagicMock()
    client.get.return_value = response
    client.__enter__.return_value = client

    with patch('app.search.service.httpx.Client', return_value=client):
      results = self.service._search_bing('Claude official site')

    self.assertEqual(len(results), 2)
    self.assertEqual(results[0].title, 'Claude')
    self.assertEqual(results[0].url, 'https://claude.ai/')

  def test_selects_brand_homepage_over_docs_page(self):
    github_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/anthropics/claude-code',
      full_name='anthropics/claude-code',
      repo_name='claude-code',
      owner_name='anthropics',
      owner_type='Organization',
      homepage='https://code.claude.com/docs/en/overview',
      description='Claude Code',
      stars=100000,
    )
    official_results = [
      SearchResultLink(title='Claude', url='https://claude.ai/'),
      SearchResultLink(
        title='Claude Code Docs',
        url='https://code.claude.com/docs/en/overview',
      ),
    ]

    selected, _, supplemental, _ = self.service._select_official_site(
      'Claude',
      official_results,
      github_candidate,
    )

    self.assertEqual(selected, 'https://claude.ai/')
    self.assertEqual(supplemental, [])

  def test_discover_targets_avoids_ambiguous_wrong_repo(self):
    github_summary = GitHubCandidateSummary(
      repo_url='https://github.com/FoundationAgents/OpenManus',
      homepage='https://openmanus.github.io',
      score=55,
      confident=False,
      reasons=['score-below-confidence-threshold'],
    )

    with (
      patch.object(self.service, '_search_github_repository', return_value=(None, github_summary)),
      patch.object(self.service, '_search_web', return_value=[]),
    ):
      targets = self.service._discover_targets('Manus', None)

    self.assertIsNone(targets)

  def test_fetch_pages_records_candidate_pages(self):
    targets = DiscoveredTargets(
      app_name='Cursor',
      official_site_url='https://cursor.com',
      github_repo_url=None,
    )
    homepage = FetchedPage(
      requested_url='https://cursor.com',
      final_url='https://cursor.com',
      html='<main><a href="/community">Community</a></main>',
      title='Cursor',
      text='community',
    )
    community = FetchedPage(
      requested_url='https://cursor.com/community',
      final_url='https://cursor.com/community',
      html='<section>Join</section>',
      title='Community',
      text='join',
    )

    with (
      patch.object(self.service, '_discover_targets', return_value=targets),
      patch.object(self.service.page_fetcher, 'fetch_page', side_effect=[homepage, community]),
      patch.object(
        self.service.page_fetcher,
        'discover_candidate_internal_links',
        return_value=[
          CandidatePageSummary(
            url='https://cursor.com/community',
            score=110,
            source_page='https://cursor.com',
            source_type='internal_link',
            reasons=['strong:community'],
          ),
        ],
      ),
      patch.object(self.service.extractor, 'extract', return_value=[]),
    ):
      _, trace = self.service.search_with_trace('https://cursor.com')

    self.assertEqual(trace.fetch.candidate_pages[0].url, 'https://cursor.com/community')
    self.assertEqual(trace.fetch.internal_links['https://cursor.com'], ['https://cursor.com/community'])

  def test_search_with_trace_uses_official_site_search_fallback(self):
    targets = DiscoveredTargets(
      app_name='MiniMax',
      official_site_url='https://www.minimaxi.com',
      github_repo_url=None,
    )
    homepage = FetchedPage(
      requested_url='https://www.minimaxi.com',
      final_url='https://www.minimaxi.com',
      html='<main>MiniMax</main>',
      title='MiniMax',
      text='MiniMax',
    )
    fallback_page = FetchedPage(
      requested_url='https://www.minimaxi.com/news/community',
      final_url='https://www.minimaxi.com/news/community',
      html='<section>加入飞书群<img src="/qr.png" /></section>',
      title='MiniMax News',
      text='加入飞书群',
    )
    fallback_group = ExtractedGroupCandidate(
      platform=Platform.FEISHU,
      group_type=GroupType.DISCUSSION,
      source_url='https://www.minimaxi.com/news/community',
      context='加入飞书群',
      image_url='/assets/qrcodes/minimax.png',
    )

    with (
      patch.object(self.service, '_discover_targets', return_value=targets),
      patch.object(self.service, '_fetch_pages', return_value=[homepage]),
      patch.object(self.service.extractor, 'extract', side_effect=[[], [fallback_group]]),
      patch.object(self.service, '_fetch_official_site_search_pages', return_value=[fallback_page]) as fallback_fetch,
    ):
      results, _ = self.service.search_with_trace('MiniMax')

    self.assertEqual(len(results), 1)
    self.assertEqual(results[0].groups[0].entry.type, 'qrcode')
    fallback_fetch.assert_called_once()

  def test_search_logs_trace_when_debug_enabled(self):
    debug_service = SearchService(
      replace(get_settings(), search_debug_enabled=True),
    )
    trace = SearchTrace(
      raw_query='Cursor',
      cleaned_query='Cursor',
      query_type='keyword',
    )

    with patch('app.search.service.logger.info') as logger_info:
      with patch.object(debug_service, 'search_with_trace', return_value=([], trace)):
        debug_service.search('Cursor')

    logger_info.assert_called_once()
    logged_payload = logger_info.call_args.args[1]
    self.assertIn('"cleaned_query": "Cursor"', logged_payload)


class SearchApiTests(unittest.TestCase):
  def setUp(self):
    self.client = TestClient(app)

  def test_search_returns_empty_message(self):
    with patch('app.api.routes.search_service.search', return_value=[]):
      response = self.client.post('/api/search', json={'query': 'Cursor'})

    self.assertEqual(response.status_code, 200)
    self.assertEqual(response.json()['empty_message'], '未发现该产品的官方群')


if __name__ == '__main__':
  unittest.main()
