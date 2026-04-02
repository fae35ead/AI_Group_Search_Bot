import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.api.schemas import GroupType, Platform
from app.core.config import get_settings
from app.search.entry_extractor import EntryExtractor
from app.search.group_type_classifier import GroupTypeClassifier
from app.search.models import (
  DiscoveryTrace,
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


class GroupTypeClassifierTests(unittest.TestCase):
  def test_classify_support_context(self):
    classifier = GroupTypeClassifier()

    self.assertEqual(
      classifier.classify('加入官方答疑群获取 Support 支持'),
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

  def test_collect_relevant_internal_links_expands_keywords_and_limit(self):
    html = """
      <main>
        <a href="/community">Community</a>
        <a href="/support">Support</a>
        <a href="/contact">Contact</a>
        <a href="/join">Join</a>
        <a href="/invite">Invite</a>
        <a href="/wechat">Wechat</a>
        <a href="/weixin">Weixin</a>
        <a href="/qq">QQ</a>
        <a href="/feishu">Feishu</a>
        <a href="/lark">Lark</a>
        <a href="/qr">QR Code</a>
        <a href="/group">Group</a>
        <a href="/forum">Forum</a>
        <a href="/shequn">社群</a>
        <a href="/dayi">答疑</a>
        <a href="/extra">加入</a>
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

    links = self.fetcher.collect_relevant_internal_links(page)

    self.assertEqual(len(links), 15)
    self.assertIn('https://example.com/join', links)
    self.assertIn('https://example.com/invite', links)
    self.assertIn('https://example.com/qr', links)
    self.assertIn('https://example.com/forum', links)
    self.assertNotIn('https://external.example.com/community', links)


class EntryExtractorTests(unittest.TestCase):
  def setUp(self):
    self.extractor = EntryExtractor(get_settings())

  def test_extracts_qrcode_candidate(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html=(
        '<section><p>加入飞书群获取最新交流信息</p>'
        '<a href="https://www.feishu.cn/invite/abc">'
        '<img src="/qr.png" alt="飞书群二维码" /></a></section>'
      ),
      title='community',
      text='加入飞书群获取最新交流信息',
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

  def test_extracts_lazy_loaded_qrcode_from_data_src(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html='<section>加入QQ群交流<img data-src="/qq-qr.png" /></section>',
      title='community',
      text='加入QQ群交流',
    )

    with (
      patch.object(self.extractor, '_download_image', return_value=(b'png', 'image/png')),
      patch.object(self.extractor, '_decode_qrcode', return_value=(None, True)),
      patch.object(self.extractor, '_store_image', return_value='/assets/qrcodes/qq.png'),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.QQ)

  def test_extracts_qrcode_from_srcset(self):
    page = FetchedPage(
      requested_url='https://example.com/community',
      final_url='https://example.com/community',
      html=(
        '<section>加入飞书群'
        '<img srcset="/small.png 1x, /large.png 2x" /></section>'
      ),
      title='community',
      text='加入飞书群',
    )

    with (
      patch.object(self.extractor, '_download_image', return_value=(b'png', 'image/png')),
      patch.object(self.extractor, '_decode_qrcode', return_value=(None, True)),
      patch.object(self.extractor, '_store_image', return_value='/assets/qrcodes/feishu.png'),
    ):
      candidates = self.extractor.extract([page])

    self.assertEqual(len(candidates), 1)
    self.assertEqual(candidates[0].platform, Platform.FEISHU)

  def test_detects_qq_group_context(self):
    platform = self.extractor._detect_platform(
      'https://example.com/invite',
      '加入QQ群获取最新答疑',
    )

    self.assertEqual(platform, Platform.QQ)

  def test_does_not_treat_generic_qq_noise_as_group(self):
    platform = self.extractor._detect_platform(
      'https://example.com/support',
      '联系 QQ 客服处理售后问题',
    )

    self.assertIsNone(platform)

  def test_extract_stats_capture_filter_counts(self):
    pages = [
      FetchedPage(
        requested_url='https://example.com/about',
        final_url='https://example.com/about',
        html='<section><a href="/about">About</a></section>',
        title='about',
        text='about',
      ),
      FetchedPage(
        requested_url='https://example.com/contact',
        final_url='https://example.com/contact',
        html='<section><a href="/contact">Community contact us</a></section>',
        title='contact',
        text='contact',
      ),
      FetchedPage(
        requested_url='https://example.com/community',
        final_url='https://example.com/community',
        html='<section><a href="/invite">加入社区</a></section>',
        title='community',
        text='community',
      ),
      FetchedPage(
        requested_url='https://example.com/qr',
        final_url='https://example.com/qr',
        html='<section>官方群二维码<img src="/not-qr.png" /></section>',
        title='qr',
        text='qr',
      ),
    ]
    stats = ExtractionStats()

    with (
      patch.object(self.extractor, '_download_image', return_value=(b'png', 'image/png')),
      patch.object(self.extractor, '_decode_qrcode', return_value=(None, False)),
    ):
      candidates = self.extractor.extract(pages, stats)

    self.assertEqual(candidates, [])
    self.assertEqual(stats.scanned_tags, 4)
    self.assertEqual(stats.contextual_candidates, 2)
    self.assertEqual(stats.filtered_positive_context, 1)
    self.assertEqual(stats.filtered_negative_context, 1)
    self.assertEqual(stats.filtered_platform_failure, 1)
    self.assertEqual(stats.filtered_qrcode_failure, 1)
    self.assertEqual(stats.output_candidates, 0)


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
    self.assertEqual(len(cards[0].groups), 1)
    self.assertEqual(cards[0].groups[0].entry.type, 'link')
    self.assertEqual(cards[0].groups[0].entry.note, '二维码暂未抓取成功')

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

  def test_normalizes_docs_homepage_to_root_candidate(self):
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

    selected, _, supplemental, _ = self.service._select_official_site(
      'Claude',
      [],
      github_candidate,
    )

    self.assertEqual(selected, 'https://code.claude.com')
    self.assertEqual(supplemental, ['https://code.claude.com/docs/en/overview'])

  def test_discover_targets_prefers_github_homepage_for_ambiguous_keyword(self):
    github_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/cursor/cursor',
      full_name='cursor/cursor',
      repo_name='cursor',
      owner_name='cursor',
      owner_type='Organization',
      homepage='https://cursor.com',
      description='Cursor editor',
      stars=32000,
    )
    github_summary = GitHubCandidateSummary(
      repo_url=github_candidate.repo_url,
      homepage=github_candidate.homepage,
      score=220,
      confident=True,
      reasons=['exact-repo-with-official-signal'],
    )

    with (
      patch.object(self.service, '_search_github_repository', return_value=(github_candidate, github_summary)),
      patch.object(
        self.service,
        '_search_web',
        return_value=[
          SearchResultLink(title='Custom Cursor', url='https://custom-cursor.com'),
        ],
      ),
    ):
      targets = self.service._discover_targets('Cursor', None)

    self.assertIsNotNone(targets)
    self.assertEqual(targets.official_site_url, 'https://cursor.com')
    self.assertEqual(targets.github_repo_url, 'https://github.com/cursor/cursor')
    self.assertEqual(targets.app_name, 'Cursor')

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

  def test_search_with_trace_returns_internal_trace(self):
    github_candidate = GitHubRepositoryCandidate(
      repo_url='https://github.com/cursor/cursor',
      full_name='cursor/cursor',
      repo_name='cursor',
      owner_name='cursor',
      owner_type='Organization',
      homepage='https://cursor.com',
      description='Cursor',
      stars=32000,
    )
    github_summary = GitHubCandidateSummary(
      repo_url=github_candidate.repo_url,
      homepage=github_candidate.homepage,
      score=220,
      confident=True,
      reasons=['exact-repo-with-official-signal'],
    )

    with (
      patch.object(
        self.service,
        '_search_web',
        return_value=[SearchResultLink(title='Cursor', url='https://cursor.com')],
      ),
      patch.object(self.service, '_search_github_repository', return_value=(github_candidate, github_summary)),
      patch.object(self.service, '_fetch_pages', return_value=[]),
    ):
      results, trace = self.service.search_with_trace('Cursor')

    self.assertEqual(results, [])
    self.assertEqual(trace.discovery.official_site_url, 'https://cursor.com')
    self.assertEqual(trace.discovery.github_repo_url, 'https://github.com/cursor/cursor')
    self.assertEqual(trace.discovery.github_candidate, github_summary)

  def test_fetch_github_metadata(self):
    response = MagicMock()
    response.json.return_value = {
      'stargazers_count': 456,
      'created_at': '2024-05-06T07:08:09Z',
      'description': 'GitHub metadata',
    }
    response.raise_for_status.return_value = None

    client = MagicMock()
    client.get.return_value = response
    client.__enter__.return_value = client

    with patch('app.search.service.httpx.Client', return_value=client):
      metadata = self.service._fetch_github_metadata('https://github.com/example/repo')

    self.assertIsNotNone(metadata)
    self.assertEqual(metadata.stars, 456)
    self.assertEqual(metadata.created_at, '2024-05-06T07:08:09Z')
    self.assertEqual(metadata.description, 'GitHub metadata')

  def test_fetch_github_metadata_returns_none_for_invalid_repo_url(self):
    metadata = self.service._fetch_github_metadata('https://github.com/example')

    self.assertIsNone(metadata)

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
