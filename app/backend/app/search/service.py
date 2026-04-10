from _thread import LockType
import base64
import csv
from dataclasses import dataclass, field
import hashlib
import json
import logging
import random
import re
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import parse_qs, parse_qsl, unquote, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from app.api.schemas import (
  GroupDiscoveryStatus,
  GroupType,
  LinkEntry,
  OfficialGroup,
  Platform,
  ProductCard,
  QQNumberEntry,
  QRCodeEntry,
  RecommendedTool,
  RecommendationsResponse,
  SearchFilters,
  SearchJobStatus,
  ViewedGroupItem,
)
from app.core.config import Settings
from app.db.database import get_connection
from app.search.entry_extractor import EntryExtractor
from app.search.models import (
  ExtractedGroupCandidate,
  ExtraVisualSource,
  FetchedPage,
  GitHubRepositoryCandidate,
  SearchResultLink,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SearchJob:
  job_id: str
  query: str
  filters: SearchFilters | None
  status: SearchJobStatus
  refresh: bool = False
  prepared_target_limit: int = 0
  cache_key: str | None = None
  remaining_candidates: list[GitHubRepositoryCandidate] = field(default_factory=list)
  raw_results: list[ProductCard] = field(default_factory=list)
  results: list[ProductCard] = field(default_factory=list)
  empty_message: str | None = None
  error: str | None = None
  created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
  updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class PreparedSearch:
  cache_key: str
  cached_results: list[ProductCard]
  merged_candidates: list[GitHubRepositoryCandidate]


@dataclass(frozen=True, slots=True)
class ViewedGroupFilters:
  viewed_ids: frozenset[str] = frozenset()
  viewed_match_keys: frozenset[str] = frozenset()


@dataclass(slots=True)
class CandidateFetchResult:
  pages: list[FetchedPage] = field(default_factory=list)
  seed_extracted_groups: list[ExtractedGroupCandidate] = field(default_factory=list)
  seed_has_groups: bool = False
  seed_page_count: int = 0

MAX_RESULTS_DEFAULT = 10
MAX_RESULTS_HARD_LIMIT = 50
INITIAL_SYNC_RESULT_LIMIT = 10
SEARCH_JOB_TARGET_LIMIT = MAX_RESULTS_HARD_LIMIT
SEARCH_JOB_HISTORY_LIMIT = 24
SEARCH_JOB_MAX_WORKERS = 2
SEARCH_JOB_BATCH_SIZE = 4
MAX_GROUPS_PER_CARD = 10
MAX_GITHUB_DEEP_CANDIDATES = 10
MAX_GITHUB_SEARCH_CANDIDATES = 20
MAX_GITHUB_DEEP_CANDIDATES_HARD_LIMIT = 80
MAX_GITHUB_SEARCH_CANDIDATES_HARD_LIMIT = 120
MAX_OWNER_EXPANSION_CANDIDATES = 2
MAX_TOPIC_EXPANSION_CANDIDATES = 3
MAX_RELATED_EXPANSION_CANDIDATES = 6
MAX_RELATED_LINKS = 4
MAX_PAGES_PER_CANDIDATE = 2 + MAX_RELATED_LINKS
MAX_WEB_FALLBACK_CANDIDATES = 8
MAX_RECOMMENDATIONS = 12
RECOMMENDATION_POOL_SIZE = 120
MAX_RECOMMENDATIONS_FETCH = 60
CANDIDATE_FETCH_MAX_WORKERS = 4
GITHUB_VARIANT_MAX_WORKERS = 4
GITHUB_SEARCH_TIMEOUT = 10.0
PAGE_FETCH_TIMEOUT = 8.0
WEB_SEARCH_TIMEOUT = 8.0
GITHUB_PRIMARY_CONFIDENCE_SCORE = 170
GITHUB_STRONG_CONFIDENCE_SCORE = 220
GITHUB_PRIMARY_EARLY_STOP_MAX_CANDIDATES = 6
CRAWL_CONFIDENT_PRIMARY_COUNT = 4
RELATED_EXPANSION_CONFIDENCE_TARGET = 5
WEB_FALLBACK_CONFIDENT_CANDIDATE_COUNT = 5
MAX_OFFICIAL_BROWSER_FALLBACK_CANDIDATES = 3
MAX_WEB_OFFICIAL_BROWSER_FALLBACK_CANDIDATES = 2
BROWSER_FETCH_TIMEOUT_MS = 20_000
BROWSER_WAIT_AFTER_LOAD_MS = 1_500
MAX_BROWSER_EXTRA_VISUAL_SOURCES = 10
MAX_BROWSER_EXTRA_DATA_URL_CHARS = 900_000
MAX_BROWSER_VISUAL_SCAN_ELEMENTS = 1200
SEARCH_CACHE_TTL_SECONDS = 24 * 60 * 60
SEARCH_EMPTY_MESSAGE = '\u672a\u5728 GitHub/\u5b98\u7f51\u76f8\u5173\u9875\u9762\u4e2d\u53d1\u73b0\u5b98\u65b9\u7fa4\u5165\u53e3'
CJK_PATTERN = re.compile(r'[\u3400-\u9fff]')
MAX_GITHUB_SEARCH_PER_PAGE = 50
LEGACY_QRCODE_FILENAME_PATTERN = re.compile(r'^[0-9a-f]{40}\.(png|jpg|jpeg|svg)$', re.IGNORECASE)
NOISY_TOKEN_FILTER_MAX_STARS = 150
NOISY_TOKEN_MIN_MATCHES = 2
GENERIC_QUERY_TOKENS = {
  'ai',
  'gpt',
  'bot',
  'chat',
  'agent',
  'assistant',
  'tool',
  'tools',
  'app',
  'apps',
  'llm',
  '模型',
  '工具',
  '助手',
  '机器人',
  '智能体',
}
COMMUNITY_INTENT_VARIANTS = (
  'community',
  'discord',
  'qq',
  'wechat',
  'feishu',
  'lark',
  'community group',
  '官方 社区',
  '社群',
  '社区',
  '交流群',
  '讨论群',
  '官方群',
)

WEB_SEARCH_VARIANTS = (
  '{query} official site',
  '{query} 官网',
  '{query} community',
  '{query} discord',
)

RECOMMENDATION_SEARCH_QUERIES = (
  'topic:ai stars:>100',
  'topic:llm stars:>50',
  'topic:agent stars:>50',
  'topic:rag stars:>50',
)

RELATED_PAGE_KEYWORDS = (
  'community',
  'support',
  'contact',
  'join',
  'docs',
  'discord',
  'qq',
  'wechat',
  'weixin',
  'feishu',
  'lark',
  'group',
  'wxwork',
  'work wechat',
  'dingtalk',
  'dingding',
  '社区',
  '社群',
  '交流群',
  '讨论群',
  '官方群',
  '加入',
  '加群',
  '入群',
  '飞书',
  '微信',
  '企业微信',
  '钉钉',
)

NOISY_TOKENS = {
  'awesome',
  'tutorial',
  'guide',
  'guides',
  'sdk',
  'boilerplate',
  'starter',
  'prompt',
  'prompts',
  'list',
  'lists',
  'collection',
  'collections',
  'resources',
  'example',
  'examples',
}

TRACKING_QUERY_KEYS = {
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_term',
  'utm_content',
  'ref',
  'source',
  'spm',
  'trk',
  'fbclid',
  'gclid',
  'igshid',
  'mc_cid',
  'mc_eid',
  'pf_rd_p',
  'pf_rd_r',
  'pd_rd_w',
  'pd_rd_wg',
  'pd_rd_r',
  'pd_rd_i',
  '_encoding',
}

OFFICIAL_SITE_BLOCKLIST = {
  'github.com',
  'www.github.com',
  'discord.com',
  'discord.gg',
  'x.com',
  'twitter.com',
  'www.twitter.com',
  'reddit.com',
  'www.reddit.com',
  'youtube.com',
  'www.youtube.com',
  'linkedin.com',
  'www.linkedin.com',
}

NOISY_RELATED_GITHUB_HOSTS = {
  'support.github.com',
  'maintainers.github.com',
}

NOISY_RELATED_GITHUB_PATH_PREFIXES = (
  '/orgs/community',
  '/contact/report-content',
  '/enterprise/premium-support',
  '/premium-support',
  '/login',
  '/topics/',
)

DISCOVERY_SIGNAL_KEYWORDS = (
  'community',
  'discord',
  'qq',
  'wechat',
  'weixin',
  'feishu',
  'lark',
  'group',
  'join',
  'invite',
  'support',
  'contact',
  '二维码',
  '社群',
  '社区',
  '官方群',
  '交流群',
  '加群',
  '入群',
  '飞书',
  '微信',
)
LOW_VALUE_RELATED_PATH_HINTS = (
  '/docs',
  '/doc',
  '/documentation',
  '/blog',
  '/changelog',
  '/release',
  '/releases',
  '/download',
  '/pricing',
  '/terms',
  '/privacy',
)


class SearchService:
  _recommendations_cache: tuple[list[RecommendedTool], datetime] | None = None
  _CACHE_TTL_SECONDS = 5 * 60

  def __init__(self, settings: Settings):
    self.settings = settings
    self.extractor = EntryExtractor(settings)
    self._page_client = httpx.Client(
      headers={'User-Agent': settings.user_agent},
      follow_redirects=True,
      timeout=PAGE_FETCH_TIMEOUT,
    )
    self._job_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=SEARCH_JOB_MAX_WORKERS)
    self._job_lock = Lock()
    self._jobs: dict[str, SearchJob] = {}
    self._browser_executor_lock = Lock()
    self._browser_executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=1)
    self._playwright = None
    self._browser = None
    self._browser_context = None

  def __del__(self):
    self.close()

  def close(self) -> None:
    try:
      self._page_client.close()
    except Exception:
      pass

    if self._job_executor is not None:
      try:
        self._job_executor.shutdown(wait=True, cancel_futures=False)
      except Exception:
        pass
      self._job_executor = None

    self._shutdown_browser_executor()

  def _ensure_job_executor(self) -> ThreadPoolExecutor:
    if self._job_executor is None:
      self._job_executor = ThreadPoolExecutor(max_workers=SEARCH_JOB_MAX_WORKERS)
    return self._job_executor

  def _ensure_browser_executor(self) -> ThreadPoolExecutor:
    with self._browser_executor_lock:
      if self._browser_executor is None:
        self._browser_executor = ThreadPoolExecutor(max_workers=1)
      return self._browser_executor

  def _shutdown_browser_executor(self) -> None:
    with self._browser_executor_lock:
      executor = self._browser_executor
      self._browser_executor = None

    if executor is None:
      return

    try:
      executor.submit(self._close_browser_worker).result(timeout=10)
    except RuntimeError:
      pass
    except Exception:
      # Best effort during shutdown.
      pass
    finally:
      try:
        executor.shutdown(wait=True, cancel_futures=False)
      except Exception:
        pass

  def _debug_log(self, message: str, *args: object) -> None:
    if self.settings.search_debug_enabled:
      logger.info('[search-debug] ' + message, *args)

  def search(
    self,
    query: str,
    filters: SearchFilters | None = None,
    *,
    refresh: bool = False,
    limit: int = MAX_RESULTS_DEFAULT,
  ) -> list[ProductCard]:
    """Deprecated compatibility wrapper around search_with_job()."""
    normalized_query = self._normalize_query(query)
    if not normalized_query:
      return []
    normalized_limit = self._normalize_result_limit(limit)
    results, job_id, _job_status, is_partial = self.search_with_job(
      normalized_query,
      filters,
      refresh=refresh,
      limit=normalized_limit,
      target_limit=normalized_limit,
      initial_sync_limit=normalized_limit,
      allow_background_job=False,
    )
    while is_partial and job_id is not None:
      time.sleep(0.1)
      job = self.get_search_job(job_id)
      if job is None:
        break
      results = job.results
      is_partial = job.status in {SearchJobStatus.PENDING, SearchJobStatus.RUNNING}
    return results[:normalized_limit]

  def search_with_job(
    self,
    query: str,
    filters: SearchFilters | None = None,
    *,
    refresh: bool = False,
    limit: int = MAX_RESULTS_DEFAULT,
    target_limit: int | None = None,
    initial_sync_limit: int | None = None,
    allow_background_job: bool = True,
  ) -> tuple[list[ProductCard], str | None, SearchJobStatus | None, bool]:
    normalized_query = self._normalize_query(query)
    if not normalized_query:
      return [], None, None, False

    normalized_limit = self._normalize_result_limit(limit)
    effective_target_limit = self._normalize_result_limit(target_limit or normalized_limit)
    initial_target = min(initial_sync_limit or INITIAL_SYNC_RESULT_LIMIT, effective_target_limit)
    viewed_filters = self._load_viewed_group_filters()
    prepared = self._prepare_search(
      normalized_query,
      filters,
      refresh=refresh,
      target_limit=effective_target_limit,
    )

    if len(prepared.cached_results) >= SEARCH_JOB_TARGET_LIMIT:
      filtered_cached = self._filter_viewed_cards(prepared.cached_results, viewed_filters=viewed_filters)
      return filtered_cached, None, SearchJobStatus.COMPLETED, False

    base_results = prepared.cached_results[:SEARCH_JOB_TARGET_LIMIT]
    exclude_product_ids = {card.product_id for card in base_results}
    remaining_candidates = [
      candidate
      for candidate in prepared.merged_candidates
      if self._candidate_product_id(candidate) not in exclude_product_ids
    ]
    sync_target = max(0, initial_target - len(base_results))

    initial_results = base_results
    if sync_target > 0 and remaining_candidates:
      sync_cards = self._collect_cards(
        remaining_candidates,
        max_cards=sync_target,
        exclude_product_ids=exclude_product_ids,
      )[:sync_target]
      initial_results = self._merge_product_cards(base_results, sync_cards, max_cards=SEARCH_JOB_TARGET_LIMIT)
      exclude_product_ids = {card.product_id for card in initial_results}
      remaining_candidates = [
        candidate
        for candidate in remaining_candidates
        if self._candidate_product_id(candidate) not in exclude_product_ids
      ]

    filtered_initial = self._filter_viewed_cards(initial_results, viewed_filters=viewed_filters)
    self._debug_log(
      'query=%r initial_results=%d remaining_candidates=%d initial_target=%d target_limit=%d allow_background_job=%s',
      normalized_query,
      len(initial_results),
      len(remaining_candidates),
      initial_target,
      effective_target_limit,
      allow_background_job,
    )

    if not allow_background_job or (not remaining_candidates and not initial_results) or (
      not remaining_candidates and len(initial_results) >= effective_target_limit
    ) or len(initial_results) >= SEARCH_JOB_TARGET_LIMIT:
      if initial_results:
        self._save_cached_search(prepared.cache_key, initial_results)
      return filtered_initial, None, SearchJobStatus.COMPLETED, False

    job = self._create_search_job(
      query=normalized_query,
      filters=filters,
      refresh=refresh,
      prepared_target_limit=effective_target_limit,
      cache_key=prepared.cache_key,
      remaining_candidates=remaining_candidates,
      raw_results=initial_results,
      results=filtered_initial,
    )
    self._ensure_job_executor().submit(self._run_search_job, job.job_id)
    return filtered_initial, job.job_id, job.status, True

  def get_search_job(self, job_id: str) -> SearchJob | None:
    with self._job_lock:
      job = self._jobs.get(job_id)
      if job is None:
        return None

      return SearchJob(
        job_id=job.job_id,
        query=job.query,
        filters=job.filters,
        status=job.status,
        refresh=job.refresh,
        prepared_target_limit=job.prepared_target_limit,
        cache_key=job.cache_key,
        remaining_candidates=job.remaining_candidates[:],
        raw_results=job.raw_results[:],
        results=job.results[:],
        empty_message=job.empty_message,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
      )

  def _prepare_search(
    self,
    normalized_query: str,
    filters: SearchFilters | None,
    *,
    refresh: bool,
    target_limit: int,
  ) -> PreparedSearch:
    cache_key = self._build_search_cache_key(normalized_query, filters)
    cached_results: list[ProductCard] = []
    if not refresh:
      cached_results = (self._load_cached_search(cache_key) or [])[:SEARCH_JOB_TARGET_LIMIT]
      if len(cached_results) >= target_limit:
        return PreparedSearch(
          cache_key=cache_key,
          cached_results=cached_results,
          merged_candidates=[],
        )

    try:
      github_candidate_limit = self._resolve_github_search_limit(target_limit)
      github_candidates = self._github_search(
        normalized_query,
        limit=github_candidate_limit,
        filters=filters,
      )
      self._debug_log(
        'query=%r github_candidates=%d candidate_limit=%d',
        normalized_query,
        len(github_candidates),
        github_candidate_limit,
      )
    except Exception as exc:
      logger.error('GitHub search failed: %s', exc)
      github_candidates = []

    deep_candidate_limit = self._resolve_github_deep_candidate_limit(target_limit)
    crawl_candidates = self._build_crawl_candidates(
      normalized_query,
      github_candidates,
      target_count=deep_candidate_limit,
    )
    include_web_fallback = self._should_include_web_fallback(
      normalized_query,
      crawl_candidates,
      target_count=deep_candidate_limit,
    )
    fallback_candidates = self._build_web_fallback_candidates(normalized_query) if include_web_fallback else []
    existing_keys = {self._candidate_key(candidate) for candidate in crawl_candidates}
    filtered_fallback = [
      candidate
      for candidate in fallback_candidates
      if self._candidate_key(candidate) not in existing_keys
    ]
    filtered_fallback.sort(
      key=lambda candidate: self._web_fallback_candidate_sort_key(normalized_query, candidate),
      reverse=True,
    )
    merged_candidates = crawl_candidates + filtered_fallback
    self._debug_log(
      'query=%r crawl_candidates=%d fallback_candidates=%d merged_candidates=%d include_web_fallback=%s',
      normalized_query,
      len(crawl_candidates),
      len(filtered_fallback),
      len(merged_candidates),
      include_web_fallback,
    )
    return PreparedSearch(
      cache_key=cache_key,
      cached_results=cached_results,
      merged_candidates=merged_candidates,
    )

  def _create_search_job(
    self,
    *,
    query: str,
    filters: SearchFilters | None,
    refresh: bool,
    prepared_target_limit: int,
    cache_key: str,
    remaining_candidates: list[GitHubRepositoryCandidate],
    raw_results: list[ProductCard],
    results: list[ProductCard],
  ) -> SearchJob:
    now = datetime.now(timezone.utc)
    job = SearchJob(
      job_id=uuid.uuid4().hex,
      query=query,
      filters=filters,
      status=SearchJobStatus.PENDING,
      refresh=refresh,
      prepared_target_limit=prepared_target_limit,
      cache_key=cache_key,
      remaining_candidates=remaining_candidates[:],
      raw_results=raw_results[:],
      results=results[:],
      created_at=now,
      updated_at=now,
    )
    with self._job_lock:
      self._jobs[job.job_id] = job
      self._trim_search_jobs_locked()
    return job

  def _trim_search_jobs_locked(self) -> None:
    if len(self._jobs) <= SEARCH_JOB_HISTORY_LIMIT:
      return

    ordered_jobs = sorted(self._jobs.values(), key=lambda item: item.updated_at)
    overflow = len(self._jobs) - SEARCH_JOB_HISTORY_LIMIT
    for item in ordered_jobs[:overflow]:
      self._jobs.pop(item.job_id, None)

  def _run_search_job(
    self,
    job_id: str,
  ) -> None:
    with self._job_lock:
      job = self._jobs.get(job_id)
      if job is None:
        return
      job.status = SearchJobStatus.RUNNING
      job.updated_at = datetime.now(timezone.utc)
      raw_results = job.raw_results[:]
      query = job.query
      filters = job.filters
      refresh = job.refresh
      prepared_target_limit = job.prepared_target_limit
      remaining_candidates = job.remaining_candidates[:]

    viewed_filters = self._load_viewed_group_filters()
    exclude_product_ids = {card.product_id for card in raw_results}
    try:
      for batch_start in range(0, len(remaining_candidates), SEARCH_JOB_BATCH_SIZE):
        remaining_capacity = SEARCH_JOB_TARGET_LIMIT - len(raw_results)
        if remaining_capacity <= 0:
          break

        batch = remaining_candidates[batch_start:batch_start + SEARCH_JOB_BATCH_SIZE]
        batch_cards = self._collect_cards(
          batch,
          max_cards=remaining_capacity,
          exclude_product_ids=exclude_product_ids,
        )[:remaining_capacity]
        if not batch_cards:
          continue

        raw_results = self._merge_product_cards(raw_results, batch_cards, max_cards=SEARCH_JOB_TARGET_LIMIT)
        exclude_product_ids = {card.product_id for card in raw_results}
        filtered_results = self._filter_viewed_cards(raw_results, viewed_filters=viewed_filters)
        with self._job_lock:
          job = self._jobs.get(job_id)
          if job is None:
            return
          job.raw_results = raw_results[:]
          job.results = filtered_results[:]
          job.updated_at = datetime.now(timezone.utc)

      if len(raw_results) < SEARCH_JOB_TARGET_LIMIT and prepared_target_limit < SEARCH_JOB_TARGET_LIMIT:
        prepared = self._prepare_search(
          query,
          filters,
          refresh=refresh,
          target_limit=SEARCH_JOB_TARGET_LIMIT,
        )
        refreshed_exclude_product_ids = {card.product_id for card in raw_results}
        remaining_candidates = [
          candidate
          for candidate in prepared.merged_candidates
          if self._candidate_product_id(candidate) not in refreshed_exclude_product_ids
        ]
        for batch_start in range(0, len(remaining_candidates), SEARCH_JOB_BATCH_SIZE):
          remaining_capacity = SEARCH_JOB_TARGET_LIMIT - len(raw_results)
          if remaining_capacity <= 0:
            break

          batch = remaining_candidates[batch_start:batch_start + SEARCH_JOB_BATCH_SIZE]
          batch_cards = self._collect_cards(
            batch,
            max_cards=remaining_capacity,
            exclude_product_ids=refreshed_exclude_product_ids,
          )[:remaining_capacity]
          if not batch_cards:
            continue

          raw_results = self._merge_product_cards(raw_results, batch_cards, max_cards=SEARCH_JOB_TARGET_LIMIT)
          refreshed_exclude_product_ids = {card.product_id for card in raw_results}
          filtered_results = self._filter_viewed_cards(raw_results, viewed_filters=viewed_filters)
          with self._job_lock:
            job = self._jobs.get(job_id)
            if job is None:
              return
            job.raw_results = raw_results[:]
            job.results = filtered_results[:]
            job.prepared_target_limit = SEARCH_JOB_TARGET_LIMIT
            job.updated_at = datetime.now(timezone.utc)

      filtered_results = self._filter_viewed_cards(raw_results, viewed_filters=viewed_filters)
      with self._job_lock:
        job = self._jobs.get(job_id)
        if job is None:
          return
        job.raw_results = raw_results[:]
        job.results = filtered_results[:]
        job.remaining_candidates = []
        job.status = SearchJobStatus.COMPLETED
        job.empty_message = SEARCH_EMPTY_MESSAGE if not filtered_results else None
        job.updated_at = datetime.now(timezone.utc)

      if raw_results:
        self._save_cached_search(job.cache_key or self._build_search_cache_key(job.query, job.filters), raw_results)
    except Exception as exc:
      logger.exception('Background search job failed: %s', exc)
      with self._job_lock:
        job = self._jobs.get(job_id)
        if job is None:
          return
        job.status = SearchJobStatus.FAILED
        job.error = str(exc)
        job.empty_message = SEARCH_EMPTY_MESSAGE if not job.results else None
        job.updated_at = datetime.now(timezone.utc)

  def _collect_cards(
    self,
    candidates: list[GitHubRepositoryCandidate],
    max_cards: int = MAX_RESULTS_HARD_LIMIT,
    exclude_product_ids: set[str] | None = None,
  ) -> list[ProductCard]:
    cards: list[ProductCard] = []
    global_seen_group_keys: set[str] = set()
    page_fetch_cache: dict[str, FetchedPage | None] = {}
    page_cache_lock = Lock()
    official_browser_budget = MAX_OFFICIAL_BROWSER_FALLBACK_CANDIDATES
    web_official_browser_budget = min(
      MAX_WEB_OFFICIAL_BROWSER_FALLBACK_CANDIDATES,
      official_browser_budget,
    )
    attempted_official_domains: set[str] = set()
    excluded = exclude_product_ids or set()
    filtered_candidates = [
      candidate for candidate in candidates if self._candidate_product_id(candidate) not in excluded
    ]
    if not filtered_candidates:
      return cards

    batch_size = min(CANDIDATE_FETCH_MAX_WORKERS, len(filtered_candidates))
    for batch_start in range(0, len(filtered_candidates), batch_size):
      if len(cards) >= max_cards:
        break

      batch = filtered_candidates[batch_start:batch_start + batch_size]
      fetch_results_by_index: list[CandidateFetchResult] = [CandidateFetchResult() for _ in batch]
      max_workers = min(CANDIDATE_FETCH_MAX_WORKERS, len(batch))
      with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
          executor.submit(
            self._fetch_candidate_pages,
            candidate,
            page_cache=page_fetch_cache,
            page_cache_lock=page_cache_lock,
          )
          for candidate in batch
        ]
        for index, (candidate, future) in enumerate(zip(batch, futures)):
          try:
            fetch_results_by_index[index] = future.result()
          except Exception as exc:
            logger.warning('Failed to fetch candidate pages for %s: %s', candidate.repo_url or candidate.homepage, exc)
            fetch_results_by_index[index] = CandidateFetchResult()

      for index, candidate in enumerate(batch):
        if len(cards) >= max_cards:
          break
        fetch_result = fetch_results_by_index[index]
        pages = fetch_result.pages
        extracted = list(fetch_result.seed_extracted_groups)
        if not fetch_result.seed_has_groups and len(pages) > fetch_result.seed_page_count:
          extracted.extend(self.extractor.extract(pages[fetch_result.seed_page_count:]))
        official_homepage_url = self._resolve_candidate_official_homepage(candidate)
        official_homepage_host = self._host_key(official_homepage_url)
        official_domain = self._domain_key(official_homepage_url)
        is_web_candidate = candidate.repo_url is None
        has_fallback_budget = (
          official_browser_budget > 0
          and (
            (is_web_candidate and web_official_browser_budget > 0)
            or (
              not is_web_candidate
              and (official_browser_budget - web_official_browser_budget) > 0
            )
          )
        )
        should_try_browser_fallback = (
          has_fallback_budget
          and official_homepage_host is not None
          and official_domain is not None
          and official_domain not in attempted_official_domains
          and self._should_try_official_browser_fallback(
            candidate=candidate,
            pages=pages,
            extracted=extracted,
            official_homepage_host=official_homepage_host,
          )
        )
        if should_try_browser_fallback and official_homepage_url:
          attempted_official_domains.add(official_domain)
          official_browser_budget -= 1
          if is_web_candidate:
            web_official_browser_budget -= 1
          browser_page = self._fetch_page_with_browser(official_homepage_url)
          if browser_page is not None:
            extracted.extend(self.extractor.extract([browser_page]))

        supported_groups = self._dedupe_groups(extracted)
        unique_groups: list[ExtractedGroupCandidate] = []
        for group in supported_groups:
          signature = self._group_signature(group)
          if signature in global_seen_group_keys:
            continue
          global_seen_group_keys.add(signature)
          unique_groups.append(group)

        supported_groups = unique_groups
        if not supported_groups:
          continue

        card = self._build_product_card(candidate, supported_groups)
        if not card.groups:
          continue
        if card.product_id in excluded:
          continue
        cards.append(card)

    return cards

  def _resolve_candidate_official_homepage(self, candidate: GitHubRepositoryCandidate) -> str | None:
    homepage = self._root_url(candidate.homepage) or candidate.homepage
    if not homepage:
      return None
    parsed = urlparse(homepage)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
      return None
    host = parsed.netloc.lower().removeprefix('www.')
    if host == 'github.com':
      return None
    return homepage

  def _should_try_official_browser_fallback(
    self,
    *,
    candidate: GitHubRepositoryCandidate,
    pages: list[FetchedPage],
    extracted: list[ExtractedGroupCandidate],
    official_homepage_host: str,
  ) -> bool:
    del candidate
    has_static_official_page = any(
      page.fetch_method == 'http' and self._host_key(page.final_url) == official_homepage_host
      for page in pages
    )
    if not has_static_official_page:
      return False

    has_official_group = any(
      any(self._host_key(source_url) == official_homepage_host for source_url in (group.source_urls or [group.source_url]))
      for group in extracted
    )
    return not has_official_group

  def _host_key(self, url: str | None) -> str | None:
    if not url:
      return None

    parsed = urlparse(url)
    host = parsed.netloc.lower().rstrip('.')
    if not host:
      return None
    if host.startswith('www.'):
      host = host[4:]
    return host

  def _domain_label(self, host: str | None) -> str:
    if not host:
      return ''
    segments = host.split('.')
    if not segments:
      return ''
    if len(segments) >= 2:
      return segments[-2]
    return segments[0]

  def _web_fallback_candidate_sort_key(
    self,
    query: str,
    candidate: GitHubRepositoryCandidate,
  ) -> tuple[int, int]:
    host = self._host_key(candidate.homepage)
    if not host:
      return (0, 0)

    query_key = query.lower().strip()
    domain_label = self._domain_label(host)
    score = 0
    if domain_label == query_key:
      score += 310
    elif domain_label.startswith(query_key):
      score += 301
    elif query_key in domain_label:
      score += 220
    elif query_key in host:
      score += 160

    if host.endswith('.com'):
      score += 35
    elif host.endswith('.cn'):
      score += 25
    elif host.endswith('.io'):
      score += 15

    return (score, -len(host))

  def _merge_product_cards(
    self,
    base_cards: list[ProductCard],
    fresh_cards: list[ProductCard],
    *,
    max_cards: int,
  ) -> list[ProductCard]:
    merged: list[ProductCard] = []
    seen_product_ids: set[str] = set()
    for card in [*base_cards, *fresh_cards]:
      if card.product_id in seen_product_ids:
        continue
      seen_product_ids.add(card.product_id)
      merged.append(card)
      if len(merged) >= max_cards:
        break
    return merged

  def _build_crawl_candidates(
    self,
    query: str,
    github_candidates: list[GitHubRepositoryCandidate],
    *,
    target_count: int | None = None,
  ) -> list[GitHubRepositoryCandidate]:
    effective_target = max(1, target_count or MAX_GITHUB_DEEP_CANDIDATES)
    primary = github_candidates[:effective_target]
    if len(primary) >= effective_target:
      return primary

    if not self._should_expand_related_candidates(query, primary, target_count=effective_target):
      return primary

    expanded = self._expand_related_candidates(query, primary, target_count=effective_target)
    seen = {self._candidate_key(candidate) for candidate in primary}
    merged = list(primary)
    for candidate in expanded:
      key = self._candidate_key(candidate)
      if key in seen:
        continue
      seen.add(key)
      merged.append(candidate)
      if len(merged) >= effective_target:
        break

    return merged

  def _expand_related_candidates(
    self,
    query: str,
    primary: list[GitHubRepositoryCandidate],
    *,
    target_count: int | None = None,
  ) -> list[GitHubRepositoryCandidate]:
    related: list[GitHubRepositoryCandidate] = []
    seen = {self._candidate_key(candidate) for candidate in primary}
    query_tokens = set(self._tokenize(query))
    effective_target = max(1, target_count or MAX_RELATED_EXPANSION_CANDIDATES)

    def reached_target() -> bool:
      if len(related) >= MAX_RELATED_EXPANSION_CANDIDATES:
        return True
      combined = [*primary, *related]
      if len(combined) >= effective_target:
        return True
      return self._has_sufficient_high_confidence_candidates(
        query,
        combined,
        min(effective_target, RELATED_EXPANSION_CONFIDENCE_TARGET),
      )

    def append_candidate(candidate: GitHubRepositoryCandidate):
      if self._should_filter(candidate):
        return
      key = self._candidate_key(candidate)
      if key in seen:
        return
      seen.add(key)
      related.append(candidate)

    owners: list[str] = []
    for candidate in primary:
      owner = candidate.owner_name.strip()
      if not owner or owner in owners:
        continue
      owners.append(owner)
      if len(owners) >= MAX_OWNER_EXPANSION_CANDIDATES:
        break

    for owner in owners:
      try:
        owner_candidates = self._github_search_onevariant(f'user:{owner}', per_page=6)
      except Exception:
        owner_candidates = []
      for candidate in owner_candidates:
        append_candidate(candidate)
        if reached_target():
          return related

    if reached_target():
      return related

    topics: list[str] = []
    for candidate in primary:
      for topic in candidate.topics:
        normalized = topic.strip().lower()
        if not normalized or normalized in topics:
          continue
        topics.append(normalized)
        if len(topics) >= MAX_TOPIC_EXPANSION_CANDIDATES:
          break
      if len(topics) >= MAX_TOPIC_EXPANSION_CANDIDATES:
        break

    for topic in topics:
      try:
        topic_candidates = self._github_search_onevariant(f'topic:{topic}', per_page=6)
      except Exception:
        topic_candidates = []
      for candidate in topic_candidates:
        candidate_tokens = set(self._tokenize(candidate.repo_name))
        if query_tokens and query_tokens.isdisjoint(candidate_tokens) and topic not in query_tokens:
          if candidate.stars < 500:
            continue
        append_candidate(candidate)
        if reached_target():
          return related

    if not reached_target():
      try:
        fallback = self._github_search_onevariant('topic:artificial-intelligence stars:>5000', per_page=6)
      except Exception:
        fallback = []
      for candidate in fallback:
        append_candidate(candidate)
        if reached_target():
          break

    related.sort(key=lambda item: item.stars, reverse=True)
    return related

  def _should_expand_related_candidates(
    self,
    query: str,
    primary: list[GitHubRepositoryCandidate],
    *,
    target_count: int,
  ) -> bool:
    if not primary:
      return False
    if len(primary) < min(target_count, CRAWL_CONFIDENT_PRIMARY_COUNT):
      return True
    return not self._has_sufficient_high_confidence_candidates(
      query,
      primary,
      min(target_count, CRAWL_CONFIDENT_PRIMARY_COUNT),
    )

  def _should_include_web_fallback(
    self,
    query: str,
    crawl_candidates: list[GitHubRepositoryCandidate],
    *,
    target_count: int,
  ) -> bool:
    if not crawl_candidates:
      return True
    if len(crawl_candidates) < min(target_count, WEB_FALLBACK_CONFIDENT_CANDIDATE_COUNT):
      return True
    return not self._has_sufficient_high_confidence_candidates(
      query,
      crawl_candidates,
      min(target_count, WEB_FALLBACK_CONFIDENT_CANDIDATE_COUNT),
    )

  def _build_web_fallback_candidates(self, query: str) -> list[GitHubRepositoryCandidate]:
    root_to_candidate: dict[str, tuple[int, GitHubRepositoryCandidate]] = {}
    for result in self._search_multi_variants(query):
      root_url = self._root_url(result.url)
      if not root_url:
        continue
      host = urlparse(root_url).netloc.lower()
      if self._is_blocked_official_host(host):
        continue

      score = self._score_web_fallback_candidate(query, result)
      existing = root_to_candidate.get(root_url)
      if existing is not None and existing[0] >= score:
        continue

      repo_name = query
      full_name = f'web/{host}'
      root_to_candidate[root_url] = (
        score,
        GitHubRepositoryCandidate(
          repo_url=None,
          full_name=full_name,
          repo_name=repo_name,
          owner_name=host,
          owner_type='Website',
          homepage=root_url,
          description=result.title,
          stars=0,
          topics=[],
          is_fork=False,
          archived=False,
          disabled=False,
        ),
      )

    ordered = sorted(root_to_candidate.values(), key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in ordered[:MAX_WEB_FALLBACK_CANDIDATES]]

  # -------------------------------------------------------------------------
  # GitHub search
  # -------------------------------------------------------------------------

  def _github_headers(self) -> dict[str, str]:
    headers = {
      'Accept': 'application/vnd.github+json',
      'User-Agent': self.settings.user_agent,
    }
    if self.settings.github_token:
      headers['Authorization'] = f'Bearer {self.settings.github_token}'
    return headers

  def _normalize_query(self, query: str) -> str:
    if not query:
      return ''

    normalized = query.strip()
    normalized = normalized.replace('\u3000', ' ')
    normalized = normalized.replace('\uff0c', ',').replace('\u3002', '.')
    normalized = normalized.replace('\uff1a', ':').replace('\uff1b', ';')
    normalized = normalized.replace('\uff08', '(').replace('\uff09', ')')
    normalized = re.sub(r'\s+', ' ', normalized)
    return normalized

  def _normalize_result_limit(self, limit: int) -> int:
    if limit < 3:
      return 3
    if limit > MAX_RESULTS_HARD_LIMIT:
      return MAX_RESULTS_HARD_LIMIT
    return limit

  def _resolve_github_search_limit(self, normalized_limit: int) -> int:
    return min(
      MAX_GITHUB_SEARCH_CANDIDATES_HARD_LIMIT,
      max(MAX_GITHUB_SEARCH_CANDIDATES, normalized_limit * 3),
    )

  def _resolve_github_deep_candidate_limit(self, normalized_limit: int) -> int:
    return min(
      MAX_GITHUB_DEEP_CANDIDATES_HARD_LIMIT,
      max(MAX_GITHUB_DEEP_CANDIDATES, normalized_limit * 2),
    )

  def _contains_cjk(self, text: str) -> bool:
    return bool(CJK_PATTERN.search(text))

  def _is_generic_query(self, query: str) -> bool:
    tokens = self._tokenize(query)
    if not tokens:
      return False
    if len(tokens) == 1:
      token = tokens[0]
      if token in GENERIC_QUERY_TOKENS:
        return True
      if token.isascii() and len(token) <= 4:
        return True
    return any(token in GENERIC_QUERY_TOKENS for token in tokens)

  def _github_search(self, query: str, limit: int, filters: SearchFilters | None = None) -> list[GitHubRepositoryCandidate]:
    contains_cjk = self._contains_cjk(query)
    is_generic_query = self._is_generic_query(query)
    primary_variants = [
      (f'{query} in:name', min(40, max(24, limit * 2))),
      (f'{query} in:description', min(28, max(16, limit + 4))),
    ]
    secondary_variants = [
      (f'{query} in:readme', min(28, max(16, limit + 4))),
    ]
    if contains_cjk:
      primary_variants = [
        (query, min(40, max(26, limit * 2))),
        (f'{query} AI', min(26, max(18, limit))),
      ] + primary_variants
    if is_generic_query:
      community_per_page = min(26, max(14, limit))
      for suffix in COMMUNITY_INTENT_VARIANTS:
        if contains_cjk and not self._contains_cjk(suffix):
          continue
        if not contains_cjk and self._contains_cjk(suffix):
          continue
        variant_query = f'{query} {suffix}' if contains_cjk else f'{query} {suffix} in:readme'
        secondary_variants.append((variant_query, community_per_page))

    unique_primary_variants = self._dedupe_github_variants(primary_variants)
    unique_secondary_variants = self._dedupe_github_variants(secondary_variants)

    all_candidates: list[GitHubRepositoryCandidate] = []
    seen_keys: set[str] = set()
    executed_variants = self._collect_github_variants(
      query=query,
      variants=unique_primary_variants,
      limit=limit,
      filters=filters,
      all_candidates=all_candidates,
      seen_keys=seen_keys,
    )
    skipped_secondary = False

    if len(all_candidates) < limit:
      should_skip_secondary = self._should_skip_github_secondary_variants(
        query=query,
        limit=limit,
        candidates=all_candidates,
        is_generic_query=is_generic_query,
      )
      if should_skip_secondary:
        skipped_secondary = True
      else:
        executed_variants += self._collect_github_variants(
          query=query,
          variants=unique_secondary_variants,
          limit=limit,
          filters=filters,
          all_candidates=all_candidates,
          seen_keys=seen_keys,
        )

    all_candidates.sort(
      key=lambda item: (
        self._score_candidate_relevance(query, item),
        item.stars,
      ),
      reverse=True,
    )
    self._debug_log(
      'github_search query=%r variants=%d unique_candidates=%d limit=%d skipped_secondary=%s',
      query,
      executed_variants,
      len(all_candidates),
      limit,
      skipped_secondary,
    )
    return all_candidates[:limit]

  def _dedupe_github_variants(self, variants: list[tuple[str, int]]) -> list[tuple[str, int]]:
    unique_variants: list[tuple[str, int]] = []
    seen_variant_queries: set[str] = set()
    for variant_query, per_page in variants:
      normalized_variant = re.sub(r'\s+', ' ', variant_query).strip()
      if not normalized_variant or normalized_variant in seen_variant_queries:
        continue
      seen_variant_queries.add(normalized_variant)
      unique_variants.append((normalized_variant, min(MAX_GITHUB_SEARCH_PER_PAGE, max(10, per_page))))
    return unique_variants

  def _collect_github_variants(
    self,
    *,
    query: str,
    variants: list[tuple[str, int]],
    limit: int,
    filters: SearchFilters | None,
    all_candidates: list[GitHubRepositoryCandidate],
    seen_keys: set[str],
  ) -> int:
    executed_variants = 0
    for chunk_start in range(0, len(variants), GITHUB_VARIANT_MAX_WORKERS):
      if len(all_candidates) >= limit:
        break

      chunk = variants[chunk_start:chunk_start + GITHUB_VARIANT_MAX_WORKERS]
      with ThreadPoolExecutor(max_workers=min(GITHUB_VARIANT_MAX_WORKERS, len(chunk))) as executor:
        chunk_futures = [
          (variant_query, per_page, executor.submit(self._github_search_onevariant, variant_query, per_page))
          for variant_query, per_page in chunk
        ]

        for variant_query, per_page, future in chunk_futures:
          if len(all_candidates) >= limit:
            break

          executed_variants += 1
          try:
            raw_candidates = future.result()
          except Exception as exc:
            logger.warning('GitHub search failed for %r: %s', variant_query, exc)
            raw_candidates = []

          duplicate_count = 0
          filtered_count = 0
          added_count = 0
          for candidate in raw_candidates:
            candidate_key = self._candidate_key(candidate)
            if candidate_key in seen_keys:
              duplicate_count += 1
              continue
            seen_keys.add(candidate_key)
            if self._should_filter(candidate, filters):
              filtered_count += 1
              continue
            all_candidates.append(candidate)
            added_count += 1
          self._debug_log(
            'github_variant=%r per_page=%d raw=%d added=%d filtered=%d duplicate=%d',
            variant_query,
            per_page,
            len(raw_candidates),
            added_count,
            filtered_count,
            duplicate_count,
          )

      all_candidates.sort(
        key=lambda item: (
          self._score_candidate_relevance(query, item),
          item.stars,
        ),
        reverse=True,
      )
    return executed_variants

  def _should_skip_github_secondary_variants(
    self,
    *,
    query: str,
    limit: int,
    candidates: list[GitHubRepositoryCandidate],
    is_generic_query: bool,
  ) -> bool:
    if len(candidates) >= limit:
      return True
    if is_generic_query:
      return False
    return self._has_sufficient_high_confidence_candidates(
      query,
      candidates,
      min(limit, GITHUB_PRIMARY_EARLY_STOP_MAX_CANDIDATES),
    )

  def _has_sufficient_high_confidence_candidates(
    self,
    query: str,
    candidates: list[GitHubRepositoryCandidate],
    target_count: int,
  ) -> bool:
    if not candidates:
      return False

    normalized_target = max(1, min(target_count, len(candidates), GITHUB_PRIMARY_EARLY_STOP_MAX_CANDIDATES))
    scores = sorted(
      (self._score_candidate_relevance(query, candidate) for candidate in candidates),
      reverse=True,
    )
    high_confidence_count = sum(1 for score in scores if score >= GITHUB_PRIMARY_CONFIDENCE_SCORE)
    strong_confidence_count = sum(1 for score in scores if score >= GITHUB_STRONG_CONFIDENCE_SCORE)
    return (
      high_confidence_count >= normalized_target
      or (scores[0] >= GITHUB_STRONG_CONFIDENCE_SCORE and strong_confidence_count >= min(2, normalized_target))
    )

  def _score_candidate_relevance(self, query: str, candidate: GitHubRepositoryCandidate) -> int:
    normalized_query = query.strip().lower()
    query_tokens = set(self._tokenize(normalized_query))

    repo_name = candidate.repo_name.lower()
    full_name = candidate.full_name.lower()
    owner_name = candidate.owner_name.lower()
    repo_tokens = set(self._tokenize(repo_name))
    full_tokens = set(self._tokenize(full_name))
    desc_tokens = set(self._tokenize(candidate.description or ''))
    topic_tokens = set(self._tokenize(' '.join(candidate.topics)))
    all_tokens = repo_tokens | full_tokens | desc_tokens | topic_tokens

    score = 0
    if normalized_query:
      if repo_name == normalized_query or full_name.endswith(f'/{normalized_query}'):
        score += 160
      if repo_name.startswith(normalized_query):
        score += 90
      if normalized_query in repo_name:
        score += 70
      if normalized_query in full_name:
        score += 50
      if normalized_query in owner_name:
        score += 12

    if query_tokens:
      overlap_repo = len(query_tokens & repo_tokens)
      overlap_full = len(query_tokens & full_tokens)
      overlap_desc = len(query_tokens & desc_tokens)
      overlap_topic = len(query_tokens & topic_tokens)
      score += overlap_repo * 35
      score += overlap_full * 20
      score += overlap_desc * 8
      score += overlap_topic * 12
      if query_tokens.issubset(repo_tokens | full_tokens):
        score += 60
      elif query_tokens.issubset(all_tokens):
        score += 25
      if query_tokens.isdisjoint(all_tokens):
        score -= 120

    score += min(candidate.stars, 50000) // 500
    return score

  # -------------------------------------------------------------------------
  # Web fallback search
  # -------------------------------------------------------------------------

  def _search_multi_variants(self, query: str) -> list[SearchResultLink]:
    deduped: list[SearchResultLink] = []
    seen_urls: set[str] = set()
    templates = list(WEB_SEARCH_VARIANTS)
    if self._contains_cjk(query):
      templates.extend(
        (
          '{query} Github',
          '{query} 开源',
          '{query} 社区',
        ),
      )
    for template in templates:
      variant = template.format(query=query)
      for result in self._search_web(variant):
        root_url = self._root_url(result.url)
        canonical = root_url or result.url
        if canonical in seen_urls:
          continue
        seen_urls.add(canonical)
        deduped.append(result)
        if len(deduped) >= 24:
          return deduped
    return deduped

  def _search_web(self, query: str) -> list[SearchResultLink]:
    results = self._search_bing(query)
    if results:
      return results
    return self._search_duckduckgo(query)

  def _search_bing(self, query: str) -> list[SearchResultLink]:
    try:
      with httpx.Client(
        headers={'User-Agent': self.settings.user_agent},
        follow_redirects=True,
        timeout=WEB_SEARCH_TIMEOUT,
      ) as client:
        response = client.get(
          'https://www.bing.com/search',
          params={'q': query, 'format': 'rss'},
        )
        response.raise_for_status()
    except httpx.HTTPError:
      return []

    try:
      root = ET.fromstring(response.text)
    except ET.ParseError:
      return []

    results: list[SearchResultLink] = []
    for item in root.findall('./channel/item'):
      title = (item.findtext('title') or '').strip()
      target_url = self._resolve_search_result_url((item.findtext('link') or '').strip())
      if not title or not target_url:
        continue
      results.append(SearchResultLink(title=title, url=target_url))
      if len(results) >= 8:
        break
    return results

  def _search_duckduckgo(self, query: str) -> list[SearchResultLink]:
    try:
      with httpx.Client(
        headers={'User-Agent': self.settings.user_agent},
        follow_redirects=True,
        timeout=WEB_SEARCH_TIMEOUT,
      ) as client:
        response = client.get('https://duckduckgo.com/html/', params={'q': query})
        if response.status_code != 200:
          return []
    except httpx.HTTPError:
      return []

    soup = BeautifulSoup(response.text, 'html.parser')
    results: list[SearchResultLink] = []
    for anchor in soup.select('a.result__a'):
      target_url = self._resolve_search_result_url(anchor.get('href', ''))
      if not target_url:
        continue
      results.append(SearchResultLink(title=anchor.get_text(' ', strip=True), url=target_url))
      if len(results) >= 8:
        break
    return results

  def _resolve_search_result_url(self, url: str) -> str:
    if not url:
      return ''

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith('bing.com'):
      encoded = parse_qs(parsed.query).get('u', [''])[0]
      if encoded.startswith('a1'):
        decoded = self._decode_bing_target(encoded[2:])
        if decoded:
          return decoded

    target = parse_qs(parsed.query).get('uddg', [''])[0]
    if target:
      return unquote(target)
    return url

  def _decode_bing_target(self, encoded: str) -> str:
    if not encoded:
      return ''
    padded = encoded + ('=' * ((4 - len(encoded) % 4) % 4))
    try:
      decoded = base64.b64decode(padded).decode('utf-8')
    except Exception:
      return ''
    return decoded if decoded.startswith(('http://', 'https://')) else ''

  def _score_web_fallback_candidate(self, query: str, result: SearchResultLink) -> int:
    parsed = urlparse(result.url)
    host = parsed.netloc.lower().removeprefix('www.')
    path = parsed.path
    query_tokens = self._tokenize(query)
    host_tokens = self._tokenize(host)
    title_tokens = self._tokenize(result.title)
    score = 0

    if query_tokens and all(token in host_tokens for token in query_tokens):
      score += 80
    elif query_tokens and all(token in title_tokens for token in query_tokens):
      score += 30

    if path in {'', '/'}:
      score += 20
    elif any(token in path.lower() for token in ('community', 'discord', 'group')):
      score += 35

    if any(token in result.title.lower() for token in ('official', '官网', 'community', 'discord')):
      score += 20

    return score

  def _tokenize(self, value: str) -> list[str]:
    prepared = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', value)
    return re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', prepared.lower())

  def _is_blocked_official_host(self, host: str) -> bool:
    normalized = host.removeprefix('www.')
    return any(
      normalized == blocked.removeprefix('www.')
      or normalized.endswith(f".{blocked.removeprefix('www.')}")
      for blocked in OFFICIAL_SITE_BLOCKLIST
    )

  def _github_search_onevariant(self, query: str, per_page: int) -> list[GitHubRepositoryCandidate]:
    try:
      with httpx.Client(
        headers=self._github_headers(),
        follow_redirects=True,
        timeout=GITHUB_SEARCH_TIMEOUT,
      ) as client:
        response = client.get(
          'https://api.github.com/search/repositories',
          params={'q': query, 'per_page': per_page, 'sort': 'stars', 'order': 'desc'},
        )
        if response.status_code in {403, 429}:
          logger.warning('GitHub API rate limited for query=%r.', query)
          raise httpx.HTTPStatusError(
            'GitHub API rate limit exceeded. Please try again later.',
            request=response.request,
            response=response,
          )
        response.raise_for_status()
    except httpx.HTTPError as exc:
      logger.warning('GitHub search failed for %r: %s', query, exc)
      return []

    candidates: list[GitHubRepositoryCandidate] = []
    for item in response.json().get('items', []):
      repo_url = item.get('html_url')
      full_name = item.get('full_name')
      owner = item.get('owner') or {}
      if not repo_url or not full_name:
        continue
      candidates.append(
        GitHubRepositoryCandidate(
          repo_url=repo_url,
          full_name=full_name,
          repo_name=item.get('name', ''),
          owner_name=owner.get('login', ''),
          owner_type=owner.get('type', ''),
          homepage=self._normalize_homepage(item.get('homepage')),
          description=item.get('description'),
          stars=item.get('stargazers_count') or 0,
          topics=item.get('topics') or [],
          pushed_at=item.get('pushed_at'),
          updated_at=item.get('updated_at'),
          created_at=item.get('created_at'),
          is_fork=bool(item.get('fork')),
          archived=bool(item.get('archived')),
          disabled=bool(item.get('disabled')),
        ),
      )

    return candidates

  def _should_filter(self, candidate: GitHubRepositoryCandidate, filters: SearchFilters | None = None) -> bool:
    if candidate.is_fork or candidate.archived or candidate.disabled:
      return True

    if filters is not None:
      if filters.min_stars is not None and candidate.stars < filters.min_stars:
        return True
      candidate_created_at = self._parse_datetime(candidate.created_at)
      if filters.created_after is not None and candidate_created_at and candidate_created_at < filters.created_after:
        return True
      if filters.created_before is not None and candidate_created_at and candidate_created_at > filters.created_before:
        return True

    haystack = ' '.join(
      [
        candidate.repo_name.lower(),
        (candidate.description or '').lower(),
        ' '.join(candidate.topics).lower(),
      ],
    )
    noisy_hits = sum(1 for token in NOISY_TOKENS if re.search(rf'(^|[\W_]){token}($|[\W_])', haystack))
    if noisy_hits >= NOISY_TOKEN_MIN_MATCHES:
      return True
    if noisy_hits == 1 and candidate.stars <= NOISY_TOKEN_FILTER_MAX_STARS:
      return True
    return False

  def _normalize_homepage(self, homepage: str | None) -> str | None:
    if not homepage:
      return None

    homepage = homepage.strip()
    if not homepage.startswith(('http://', 'https://')):
      return None

    homepage = homepage.rstrip('/')
    if homepage.endswith('.'):
      homepage = homepage[:-1]

    host = urlparse(homepage).netloc.lower()
    if host in {'github.com', 'www.github.com'}:
      return None

    return homepage

  def _candidate_key(self, candidate: GitHubRepositoryCandidate) -> str:
    return (
      candidate.repo_url
      or self._root_url(candidate.homepage)
      or candidate.homepage
      or candidate.full_name.lower()
    )

  def _candidate_product_id(self, candidate: GitHubRepositoryCandidate) -> str:
    return hashlib.sha1((candidate.repo_url or candidate.full_name).encode('utf-8')).hexdigest()[:12]

  def _build_search_cache_key(self, query: str, filters: SearchFilters | None) -> str:
    payload = {
      'query': query.strip().lower(),
      'filters': self._serialize_filters(filters),
      'version': 5,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()

  def _serialize_filters(self, filters: SearchFilters | None) -> dict[str, str | int] | None:
    if filters is None:
      return None

    serialized: dict[str, str | int] = {}
    if filters.min_stars is not None:
      serialized['min_stars'] = filters.min_stars
    if filters.created_after is not None:
      serialized['created_after'] = filters.created_after.isoformat()
    if filters.created_before is not None:
      serialized['created_before'] = filters.created_before.isoformat()
    return serialized or None

  def _load_cached_search(self, query_key: str) -> list[ProductCard] | None:
    now = datetime.now(timezone.utc)
    try:
      with get_connection(self.settings.database_path) as connection:
        row = connection.execute(
          '''
          SELECT response_json, updated_at
          FROM search_cache
          WHERE query_key = ?
          ''',
          (query_key,),
        ).fetchone()
    except Exception as exc:
      logger.warning('Failed to read search cache: %s', exc)
      return None

    if row is None:
      return None

    cached_at = self._parse_datetime(row['updated_at'])
    if cached_at is None:
      return None
    if cached_at.tzinfo is None:
      cached_at = cached_at.replace(tzinfo=timezone.utc)

    if (now - cached_at).total_seconds() > SEARCH_CACHE_TTL_SECONDS:
      return None

    try:
      payload = json.loads(row['response_json'])
      if isinstance(payload, list) and not payload:
        return None
      cards = [ProductCard.model_validate(item) for item in payload]
      normalized_cards, changed = self._normalize_qrcode_paths_in_cards(cards)
      if changed:
        self._save_cached_search(query_key, normalized_cards)
      return normalized_cards
    except Exception as exc:
      logger.warning('Failed to parse cached search response: %s', exc)
      return None

  def _save_cached_search(self, query_key: str, results: list[ProductCard]) -> None:
    payload = [item.model_dump(mode='json') for item in results]
    now = datetime.now(timezone.utc).isoformat()
    try:
      with get_connection(self.settings.database_path) as connection:
        connection.execute(
          '''
          INSERT INTO search_cache (query_key, response_json, updated_at)
          VALUES (?, ?, ?)
          ON CONFLICT(query_key) DO UPDATE SET
            response_json = excluded.response_json,
            updated_at = excluded.updated_at
          ''',
          (query_key, json.dumps(payload, ensure_ascii=False), now),
        )
        connection.commit()
    except Exception as exc:
      logger.warning('Failed to write search cache: %s', exc)

  def _normalize_qrcode_paths_in_cards(self, cards: list[ProductCard]) -> tuple[list[ProductCard], bool]:
    changed = False
    for card in cards:
      for group in card.groups:
        canonical_platform = self._canonicalize_platform(group.platform)
        if canonical_platform != group.platform:
          group.platform = canonical_platform
          changed = True
        if group.entry.type != 'qrcode':
          continue
        normalized = self._normalize_legacy_qrcode_path(
          group.entry.image_path,
          app_name=card.app_name,
          platform=canonical_platform,
        )
        if normalized == group.entry.image_path:
          continue
        group.entry.image_path = normalized
        changed = True
    return cards, changed

  def _canonicalize_platform(self, platform: Platform) -> Platform:
    if platform == Platform.WECOM:
      return Platform.WECHAT
    return platform

  def _safe_qrcode_name(self, value: str, *, fallback: str) -> str:
    sanitized = ''.join(ch for ch in value if ch.isalnum() or ch in '-_').strip()
    return sanitized or fallback

  def _ensure_viewed_export_paths(self) -> None:
    self.settings.viewed_dir.mkdir(parents=True, exist_ok=True)
    self.settings.viewed_qrcode_dir.mkdir(parents=True, exist_ok=True)

  def _build_viewed_qrcode_export_filename(
    self,
    *,
    app_name: str,
    platform: Platform,
    view_key: str,
    source_filename: str,
  ) -> str:
    extension = source_filename.rsplit('.', 1)[-1].lower() if '.' in source_filename else 'png'
    safe_name = self._safe_qrcode_name(app_name, fallback='repo')
    safe_platform = self._safe_qrcode_name(self._canonicalize_platform(platform).value, fallback='platform')
    safe_view_key = self._safe_qrcode_name(view_key[:8], fallback='viewed')
    return f'{safe_name}_{safe_platform}_{safe_view_key}.{extension}'

  def _resolve_qrcode_asset_source(self, image_path: str | None) -> tuple[str, str] | None:
    normalized_path = (image_path or '').strip()
    if not normalized_path.startswith('/assets/qrcodes/'):
      return None

    filename = normalized_path.rsplit('/', 1)[-1]
    if not filename:
      return None

    return filename, str(self.settings.qrcode_dir / filename)

  def _sync_viewed_exports(self) -> None:
    self._ensure_viewed_export_paths()
    expected_qrcode_filenames: set[str] = set()
    csv_rows: list[dict[str, str]] = []
    normalized_image_updates: list[tuple[str, str]] = []
    canonical_platform_updates: list[tuple[str, Platform]] = []

    with get_connection(self.settings.database_path) as connection:
      rows = connection.execute(
        '''
        SELECT
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
        FROM viewed_groups
        ORDER BY viewed_at DESC
        ''',
      ).fetchall()

    for row in rows:
      try:
        raw_platform = Platform(str(row['platform']))
        group_type = GroupType(str(row['group_type']))
      except ValueError:
        continue
      platform = self._canonicalize_platform(raw_platform)
      if platform != raw_platform:
        canonical_platform_updates.append((str(row['view_key']), platform))

      view_key = str(row['view_key'])
      app_name = str(row['app_name'])
      entry_type = str(row['entry_type'])
      entry_url = str(row['entry_url'] or '').strip()
      fallback_url = str(row['fallback_url'] or '').strip()
      viewed_at = str(row['viewed_at'] or '').strip()

      if entry_type == 'qrcode':
        image_path = str(row['image_path'] or '').strip()
        if not image_path:
          continue

        normalized_image_path = self._normalize_legacy_qrcode_path(
          image_path,
          app_name=app_name,
          platform=platform,
        )
        if normalized_image_path != image_path:
          normalized_image_updates.append((view_key, normalized_image_path))

        resolved_source = self._resolve_qrcode_asset_source(normalized_image_path)
        if resolved_source is None:
          logger.warning(
            'Skipping viewed qrcode export with unsupported image path: %s',
            normalized_image_path,
          )
          continue

        source_filename, source_path_raw = resolved_source
        export_filename = self._build_viewed_qrcode_export_filename(
          app_name=app_name,
          platform=platform,
          view_key=view_key,
          source_filename=source_filename,
        )
        expected_qrcode_filenames.add(export_filename)
        export_path = self.settings.viewed_qrcode_dir / export_filename
        source_path = self.settings.qrcode_dir / source_filename

        if source_path.exists():
          if not export_path.exists():
            shutil.copyfile(source_path, export_path)
        else:
          logger.warning(
            'Skipping viewed qrcode export because source file is missing: %s',
            source_path_raw,
          )
        continue

      if entry_type not in {'link', 'qq_number'} or not entry_url:
        continue

      csv_rows.append(
        {
          'view_key': view_key,
          'product_id': str(row['product_id']),
          'app_name': app_name,
          'platform': platform.value,
          'group_type': group_type.value,
          'entry_type': entry_type,
          'entry_value': entry_url,
          'fallback_url': fallback_url,
          'viewed_at': viewed_at,
        },
      )

    self._update_viewed_group_image_paths(normalized_image_updates)
    self._update_viewed_group_platforms(canonical_platform_updates)

    for existing_file in self.settings.viewed_qrcode_dir.iterdir():
      if existing_file.is_file() and existing_file.name not in expected_qrcode_filenames:
        existing_file.unlink()

    with self.settings.viewed_links_csv_path.open('w', encoding='utf-8-sig', newline='') as csv_file:
      writer = csv.DictWriter(
        csv_file,
        fieldnames=[
          'view_key',
          'product_id',
          'app_name',
          'platform',
          'group_type',
          'entry_type',
          'entry_value',
          'fallback_url',
          'viewed_at',
        ],
      )
      writer.writeheader()
      writer.writerows(csv_rows)

  def _sync_viewed_exports_safely(self) -> None:
    try:
      self._sync_viewed_exports()
    except Exception as exc:
      logger.warning('Failed to sync viewed exports: %s', exc)

  def _normalize_legacy_qrcode_path(self, image_path: str, *, app_name: str, platform: Platform) -> str:
    normalized_path = image_path.strip()
    if not normalized_path.startswith('/assets/qrcodes/'):
      return image_path

    filename = normalized_path.rsplit('/', 1)[-1]
    source = self.settings.qrcode_dir / filename
    if not source.exists():
      return image_path

    if LEGACY_QRCODE_FILENAME_PATTERN.fullmatch(filename):
      digest = source.stem[:8]
    else:
      stem_parts = [part for part in source.stem.split('_') if part]
      digest = (stem_parts[-1] if stem_parts else source.stem)[-8:]
      if not digest:
        digest = source.stem[:8]
    extension = source.suffix.lstrip('.').lower() or 'png'
    safe_name = self._safe_qrcode_name(app_name, fallback='repo')
    safe_platform = self._safe_qrcode_name(self._canonicalize_platform(platform).value, fallback='platform')
    target_filename = f'{safe_name}_{safe_platform}_{digest}.{extension}'
    target = self.settings.qrcode_dir / target_filename

    if target_filename == filename:
      return normalized_path
    if target.exists():
      return f'/assets/qrcodes/{target_filename}'

    try:
      source.rename(target)
    except OSError:
      return image_path
    return f'/assets/qrcodes/{target_filename}'

  def _update_viewed_group_image_paths(self, updates: list[tuple[str, str]]) -> None:
    if not updates:
      return
    try:
      with get_connection(self.settings.database_path) as connection:
        connection.executemany(
          'UPDATE viewed_groups SET image_path = ? WHERE view_key = ?',
          [(image_path, view_key) for view_key, image_path in updates],
        )
        connection.commit()
    except Exception as exc:
      logger.warning('Failed to update viewed group image paths: %s', exc)

  def _update_viewed_group_platforms(self, updates: list[tuple[str, Platform]]) -> None:
    if not updates:
      return
    try:
      with get_connection(self.settings.database_path) as connection:
        connection.executemany(
          'UPDATE viewed_groups SET platform = ? WHERE view_key = ?',
          [(platform.value, view_key) for view_key, platform in updates],
        )
        connection.commit()
    except Exception as exc:
      logger.warning('Failed to update viewed group platforms: %s', exc)

  def _build_viewed_group_match_key(
    self,
    *,
    product_id: str,
    platform: Platform,
    entry_type: str,
    entry_url: str | None = None,
    image_path: str | None = None,
  ) -> str | None:
    canonical_platform = self._canonicalize_platform(platform)
    if entry_type == 'qq_number':
      qq_number = (entry_url or '').strip()
      if not qq_number:
        return None
      return f'{product_id}:{canonical_platform.value}:qq_number:{qq_number}'

    if entry_type == 'qrcode':
      normalized_path = (image_path or '').strip()
      if not normalized_path:
        return None
      return f'{product_id}:{canonical_platform.value}:qrcode:{normalized_path}'

    if entry_type == 'link':
      normalized_link = self._normalize_group_link((entry_url or '').strip(), canonical_platform)
      if not normalized_link:
        normalized_link = (entry_url or '').strip()
      if not normalized_link:
        return None
      return f'{product_id}:{canonical_platform.value}:link:{normalized_link}'

    return None

  def _load_viewed_group_filters(self) -> ViewedGroupFilters:
    viewed_ids: set[str] = set()
    viewed_match_keys: set[str] = set()
    normalized_image_updates: list[tuple[str, str]] = []
    canonical_platform_updates: list[tuple[str, Platform]] = []
    try:
      with get_connection(self.settings.database_path) as connection:
        rows = connection.execute(
          '''
          SELECT
            view_key,
            product_id,
            app_name,
            platform,
            entry_type,
            entry_url,
            image_path
          FROM viewed_groups
          ''',
        ).fetchall()
    except Exception as exc:
      logger.warning('Failed to read viewed groups: %s', exc)
      return ViewedGroupFilters()

    for row in rows:
      view_key = str(row['view_key'])
      viewed_ids.add(view_key)
      try:
        raw_platform = Platform(str(row['platform']))
      except ValueError:
        continue
      platform = self._canonicalize_platform(raw_platform)
      if platform != raw_platform:
        canonical_platform_updates.append((view_key, platform))

      entry_type = str(row['entry_type'] or '').strip()
      image_path = str(row['image_path'] or '').strip()
      if entry_type == 'qrcode' and image_path:
        normalized_path = self._normalize_legacy_qrcode_path(
          image_path,
          app_name=str(row['app_name']),
          platform=platform,
        )
        if normalized_path != image_path:
          normalized_image_updates.append((view_key, normalized_path))
          image_path = normalized_path

      match_key = self._build_viewed_group_match_key(
        product_id=str(row['product_id']),
        platform=platform,
        entry_type=entry_type,
        entry_url=str(row['entry_url'] or '').strip(),
        image_path=image_path,
      )
      if match_key:
        viewed_match_keys.add(match_key)

    self._update_viewed_group_image_paths(normalized_image_updates)
    self._update_viewed_group_platforms(canonical_platform_updates)
    return ViewedGroupFilters(
      viewed_ids=frozenset(viewed_ids),
      viewed_match_keys=frozenset(viewed_match_keys),
    )

  def _filter_viewed_cards(
    self,
    cards: list[ProductCard],
    *,
    viewed_filters: ViewedGroupFilters | None = None,
  ) -> list[ProductCard]:
    resolved_filters = viewed_filters if viewed_filters is not None else self._load_viewed_group_filters()
    if not resolved_filters.viewed_ids and not resolved_filters.viewed_match_keys:
      return cards

    filtered: list[ProductCard] = []
    for card in cards:
      remaining_groups = [
        group
        for group in card.groups
        if (
          group.group_id not in resolved_filters.viewed_ids
          and self._build_viewed_group_match_key(
            product_id=card.product_id,
            platform=group.platform,
            entry_type=group.entry.type,
            entry_url=(
              group.entry.qq_number
              if group.entry.type == 'qq_number'
              else group.entry.url
              if group.entry.type == 'link'
              else None
            ),
            image_path=group.entry.image_path if group.entry.type == 'qrcode' else None,
          ) not in resolved_filters.viewed_match_keys
        )
      ]
      if not remaining_groups:
        continue

      filtered.append(
        card.model_copy(
          update={
            'groups': remaining_groups,
            'group_discovery_status': GroupDiscoveryStatus.FOUND,
          },
        ),
      )
    return filtered

  def mark_group_viewed(
    self,
    product_id: str,
    app_name: str,
    group: OfficialGroup,
    *,
    is_ignored: bool = False,
  ) -> None:
    if not group.group_id:
      return

    entry_url: str | None = None
    image_path: str | None = None
    fallback_url: str | None = None
    if group.entry.type == 'qrcode':
      image_path = group.entry.image_path
      fallback_url = group.entry.fallback_url
    elif group.entry.type == 'qq_number':
      entry_url = group.entry.qq_number
    else:
      entry_url = group.entry.url

    platform = self._canonicalize_platform(group.platform)
    if image_path:
      image_path = self._normalize_legacy_qrcode_path(
        image_path,
        app_name=app_name,
        platform=platform,
      )

    now = datetime.now(timezone.utc).isoformat()
    try:
      with get_connection(self.settings.database_path) as connection:
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
            viewed_at,
            is_ignored
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(view_key) DO UPDATE SET
            product_id = excluded.product_id,
            app_name = excluded.app_name,
            platform = excluded.platform,
            group_type = excluded.group_type,
            entry_type = excluded.entry_type,
            entry_url = excluded.entry_url,
            image_path = excluded.image_path,
            fallback_url = excluded.fallback_url,
            viewed_at = excluded.viewed_at,
            is_ignored = excluded.is_ignored
          ''',
          (
            group.group_id,
            product_id,
            app_name,
            platform.value,
            group.group_type.value,
            group.entry.type,
            entry_url,
            image_path,
            fallback_url,
            now,
            int(is_ignored),
          ),
        )
        connection.commit()
    except Exception as exc:
      logger.warning('Failed to mark group viewed: %s', exc)
      return

    self._sync_viewed_exports_safely()

  def manual_upload_group(
    self,
    *,
    app_name: str,
    description: str | None,
    created_at: str | None,
    github_stars: int | None,
    platform: Platform,
    group_type: GroupType,
    entry_type: str,
    entry_url: str | None,
    fallback_url: str | None,
    qrcode_bytes: bytes | None,
    qrcode_content_type: str | None,
  ) -> str:
    normalized_app_name = app_name.strip()
    if not normalized_app_name:
      raise ValueError('app_name is required')

    normalized_entry_type = entry_type.strip().lower()
    if normalized_entry_type not in {'qrcode', 'link'}:
      raise ValueError('entry_type must be qrcode or link')

    normalized_entry_url = entry_url.strip() if entry_url else None
    normalized_fallback = fallback_url.strip() if fallback_url else None
    if normalized_entry_type == 'link' and not normalized_entry_url:
      raise ValueError('entry_url is required when entry_type is link')
    if normalized_entry_type == 'qrcode' and not qrcode_bytes:
      raise ValueError('qrcode_file is required when entry_type is qrcode')

    canonical_platform = self._canonicalize_platform(platform)

    image_path: str | None = None
    if normalized_entry_type == 'qrcode' and qrcode_bytes:
      image_path = self._save_qr_code(
        image_bytes=qrcode_bytes,
        platform=canonical_platform,
        repo_name=normalized_app_name,
        content_type=qrcode_content_type,
      )

    identity = image_path or normalized_entry_url or normalized_fallback or normalized_app_name.lower()
    stable_seed = f'manual:{normalized_app_name.lower()}:{canonical_platform.value}:{normalized_entry_type}:{identity}'
    view_key = hashlib.sha1(stable_seed.encode('utf-8')).hexdigest()[:16]
    product_id = hashlib.sha1(f'manual:{normalized_app_name.lower()}'.encode('utf-8')).hexdigest()[:12]

    normalized_description = description.strip() if description else None
    normalized_created_at = created_at.strip() if created_at else None
    normalized_stars = github_stars if github_stars is not None and github_stars >= 0 else None
    now = datetime.now(timezone.utc).isoformat()

    try:
      with get_connection(self.settings.database_path) as connection:
        connection.execute(
          '''
          INSERT INTO manual_uploads (
            view_key,
            app_name,
            description,
            created_at,
            github_stars,
            platform,
            group_type,
            entry_type,
            entry_url,
            image_path,
            fallback_url,
            uploaded_at
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(view_key) DO UPDATE SET
            app_name = excluded.app_name,
            description = excluded.description,
            created_at = excluded.created_at,
            github_stars = excluded.github_stars,
            platform = excluded.platform,
            group_type = excluded.group_type,
            entry_type = excluded.entry_type,
            entry_url = excluded.entry_url,
            image_path = excluded.image_path,
            fallback_url = excluded.fallback_url,
            uploaded_at = excluded.uploaded_at
          ''',
          (
            view_key,
            normalized_app_name,
            normalized_description,
            normalized_created_at,
            normalized_stars,
            canonical_platform.value,
            group_type.value,
            normalized_entry_type,
            normalized_entry_url,
            image_path,
            normalized_fallback,
            now,
          ),
        )

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
            viewed_at,
            is_ignored
          ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(view_key) DO UPDATE SET
            product_id = excluded.product_id,
            app_name = excluded.app_name,
            platform = excluded.platform,
            group_type = excluded.group_type,
            entry_type = excluded.entry_type,
            entry_url = excluded.entry_url,
            image_path = excluded.image_path,
            fallback_url = excluded.fallback_url,
            viewed_at = excluded.viewed_at,
            is_ignored = 0
          ''',
          (
            view_key,
            product_id,
            normalized_app_name,
            canonical_platform.value,
            group_type.value,
            normalized_entry_type,
            normalized_entry_url,
            image_path,
            normalized_fallback,
            now,
            0,
          ),
        )
        connection.commit()
    except Exception as exc:
      logger.warning('Failed to write manual upload: %s', exc)
      raise ValueError('manual upload failed') from exc

    self._sync_viewed_exports_safely()
    return view_key

  def remove_viewed_group(self, view_key: str) -> None:
    if not view_key:
      return
    try:
      with get_connection(self.settings.database_path) as connection:
        connection.execute(
          'DELETE FROM viewed_groups WHERE view_key = ?',
          (view_key,),
        )
        connection.commit()
    except Exception as exc:
      logger.warning('Failed to remove viewed group: %s', exc)
      return

    self._sync_viewed_exports_safely()

  def list_viewed_groups(self) -> list[ViewedGroupItem]:
    try:
      with get_connection(self.settings.database_path) as connection:
        rows = connection.execute(
          '''
          SELECT
            view_key,
            product_id,
            app_name,
            platform,
            group_type,
            entry_type,
            entry_url,
            image_path,
            fallback_url,
            viewed_at,
            is_joined,
            is_ignored
          FROM viewed_groups
          ORDER BY viewed_at DESC
          ''',
        ).fetchall()
    except Exception as exc:
      logger.warning('Failed to list viewed groups: %s', exc)
      return []

    items: list[ViewedGroupItem] = []
    normalized_image_updates: list[tuple[str, str]] = []
    canonical_platform_updates: list[tuple[str, Platform]] = []
    for row in rows:
      try:
        raw_platform = Platform(str(row['platform']))
        group_type = GroupType(str(row['group_type']))
      except ValueError:
        continue
      platform = self._canonicalize_platform(raw_platform)
      view_key = str(row['view_key'])
      if platform != raw_platform:
        canonical_platform_updates.append((view_key, platform))

      entry_type = str(row['entry_type'])
      if entry_type == 'qrcode':
        image_path = str(row['image_path'] or '')
        if not image_path:
          continue
        normalized_path = self._normalize_legacy_qrcode_path(
          image_path,
          app_name=str(row['app_name']),
          platform=platform,
        )
        if normalized_path != image_path:
          normalized_image_updates.append((view_key, normalized_path))
        entry = QRCodeEntry(
          type='qrcode',
          image_path=normalized_path,
          fallback_url=row['fallback_url'],
        )
      elif entry_type == 'link':
        entry_url = str(row['entry_url'] or '')
        if not entry_url:
          continue
        entry = LinkEntry(type='link', url=entry_url)
      elif entry_type == 'qq_number':
        qq_number = str(row['entry_url'] or '').strip()
        if not qq_number:
          continue
        entry = QQNumberEntry(type='qq_number', qq_number=qq_number)
      else:
        continue

      viewed_at = self._parse_datetime(row['viewed_at']) or datetime.now(timezone.utc)
      if viewed_at.tzinfo is None:
        viewed_at = viewed_at.replace(tzinfo=timezone.utc)

      items.append(
        ViewedGroupItem(
          view_key=view_key,
          product_id=str(row['product_id']),
          app_name=str(row['app_name']),
          platform=platform,
          group_type=group_type,
          entry=entry,
          viewed_at=viewed_at,
          is_joined=bool(row['is_joined']),
          is_ignored=bool(row['is_ignored']),
        ),
      )

    self._update_viewed_group_image_paths(normalized_image_updates)
    self._update_viewed_group_platforms(canonical_platform_updates)
    if normalized_image_updates or canonical_platform_updates:
      self._sync_viewed_exports_safely()
    return items

  def toggle_group_joined(self, view_key: str) -> bool:
    if not view_key:
      return False
    try:
      with get_connection(self.settings.database_path) as connection:
        connection.execute(
          'UPDATE viewed_groups SET is_joined = 1 - is_joined WHERE view_key = ?',
          (view_key,),
        )
        connection.commit()
        row = connection.execute(
          'SELECT is_joined FROM viewed_groups WHERE view_key = ?',
          (view_key,),
        ).fetchone()
        return bool(row['is_joined']) if row else False
    except Exception as exc:
      logger.warning('Failed to toggle group joined: %s', exc)
      return False

  def toggle_group_ignored(self, view_key: str) -> bool:
    if not view_key:
      return False
    try:
      with get_connection(self.settings.database_path) as connection:
        connection.execute(
          'UPDATE viewed_groups SET is_ignored = 1 - is_ignored WHERE view_key = ?',
          (view_key,),
        )
        connection.commit()
        row = connection.execute(
          'SELECT is_ignored FROM viewed_groups WHERE view_key = ?',
          (view_key,),
        ).fetchone()
        return bool(row['is_ignored']) if row else False
    except Exception as exc:
      logger.warning('Failed to toggle group ignored: %s', exc)
      return False

  def bulk_mark_viewed(self, items: list) -> int:
    """Mark multiple groups as viewed. Calls mark_group_viewed for each item."""
    count = 0
    for item in items:
      try:
        self.mark_group_viewed(
          product_id=item.product_id,
          app_name=item.app_name,
          group=item.group,
          is_ignored=getattr(item, 'is_ignored', False),
        )
        count += 1
      except Exception as exc:
        logger.warning('Failed to bulk mark viewed item: %s', exc)
    return count

  # -------------------------------------------------------------------------
  # Page fetching
  # -------------------------------------------------------------------------

  def _fetch_candidate_pages(
    self,
    candidate: GitHubRepositoryCandidate,
    *,
    page_cache: dict[str, FetchedPage | None] | None = None,
    page_cache_lock: LockType | None = None,
  ) -> CandidateFetchResult:
    pages: list[FetchedPage] = []
    seed_urls: list[str] = []
    seen_urls: set[str] = set()

    def remember_url(url: str | None) -> str | None:
      if not url:
        return None
      normalized = url.rstrip('/')
      if not normalized or normalized in seen_urls:
        return None
      seen_urls.add(normalized)
      return normalized

    def fetch_cached_page(url: str) -> FetchedPage | None:
      cache_hit = False
      page = None
      if page_cache is not None:
        if page_cache_lock is not None:
          with page_cache_lock:
            cache_hit = url in page_cache
            page = page_cache.get(url)
        else:
          cache_hit = url in page_cache
          page = page_cache.get(url)
      if not cache_hit:
        page = self._fetch_page(url)
        if page_cache is not None:
          if page_cache_lock is not None:
            with page_cache_lock:
              page_cache[url] = page
              if page is not None:
                page_cache[page.final_url.rstrip('/')] = page
          else:
            page_cache[url] = page
            if page is not None:
              page_cache[page.final_url.rstrip('/')] = page
      return page

    for seed in (candidate.repo_url, candidate.homepage):
      normalized_seed = remember_url(seed)
      if normalized_seed is not None:
        seed_urls.append(normalized_seed)

    for seed_url in seed_urls:
      if len(pages) >= MAX_PAGES_PER_CANDIDATE:
        break
      page = fetch_cached_page(seed_url)
      if page is not None:
        pages.append(page)

    if not pages:
      return CandidateFetchResult()

    seed_page_count = len(pages)

    if not any(self._page_has_discovery_signal(page) for page in pages):
      return CandidateFetchResult(
        pages=pages,
        seed_page_count=seed_page_count,
      )

    seed_groups = self._dedupe_groups(self.extractor.extract(pages))
    if seed_groups:
      return CandidateFetchResult(
        pages=pages,
        seed_extracted_groups=seed_groups,
        seed_has_groups=True,
        seed_page_count=seed_page_count,
      )

    queue: list[str] = []
    related_pages_added = 0
    for page in pages:
      if not self._page_supports_related_expansion(page):
        continue
      for related_url in self._collect_relevant_links(page, candidate):
        if related_pages_added >= MAX_RELATED_LINKS:
          break
        if len(pages) + len(queue) >= MAX_PAGES_PER_CANDIDATE:
          break
        normalized_related = remember_url(related_url)
        if normalized_related is None:
          continue
        queue.append(normalized_related)
        related_pages_added += 1

    while queue and len(pages) < MAX_PAGES_PER_CANDIDATE:
      current = queue.pop(0)
      page = fetch_cached_page(current)
      if page is None:
        continue
      pages.append(page)

    return CandidateFetchResult(
      pages=pages,
      seed_extracted_groups=seed_groups,
      seed_has_groups=bool(seed_groups),
      seed_page_count=seed_page_count,
    )

  def _page_has_discovery_signal(self, page: FetchedPage) -> bool:
    haystacks = [page.title, page.text, page.final_url, page.requested_url]
    soup = page.soup or BeautifulSoup(page.html, 'html.parser')
    for anchor in soup.find_all('a', href=True)[:20]:
      haystacks.append(anchor.get('href', ''))
      haystacks.append(anchor.get_text(' ', strip=True))
    for image in soup.find_all('img')[:12]:
      haystacks.append(image.get('alt', ''))
      haystacks.append(image.get('title', ''))
      haystacks.append(image.get('src', ''))

    combined = ' '.join(part for part in haystacks if part).lower()
    return any(keyword in combined for keyword in DISCOVERY_SIGNAL_KEYWORDS)

  def _page_supports_related_expansion(self, page: FetchedPage) -> bool:
    return self._page_has_discovery_signal(page)

  def _fetch_page(self, url: str) -> FetchedPage | None:
    try:
      response = self._page_client.get(url)
      if response.status_code in {403, 404, 429}:
        return None
      response.raise_for_status()
    except httpx.HTTPError as exc:
      logger.warning('Failed to fetch %r: %s', url, exc)
      return None

    html = response.text
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.title.string.strip() if soup.title and soup.title.string else ''
    return FetchedPage(
      requested_url=url,
      final_url=str(response.url),
      html=html,
      title=title,
      text=soup.get_text(' ', strip=True)[:2000],
      fetch_method='http',
      soup=soup,
    )

  def _fetch_page_with_browser(self, url: str) -> FetchedPage | None:
    executor = self._ensure_browser_executor()
    try:
      future = executor.submit(self._fetch_page_with_browser_worker, url)
      return future.result()
    except RuntimeError as exc:
      logger.warning('Browser worker is unavailable for %r: %s', url, exc)
      return None
    except Exception as exc:
      logger.warning('Browser fetch failed for %r: %s', url, exc)
      return None

  def _fetch_page_with_browser_worker(self, url: str) -> FetchedPage | None:
    try:
      from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except Exception as exc:
      logger.warning('Playwright is unavailable for %r: %s', url, exc)
      return None

    context = self._ensure_browser_context_worker()
    if context is None:
      return None

    page = context.new_page()
    try:
      page.goto(url, wait_until='domcontentloaded', timeout=BROWSER_FETCH_TIMEOUT_MS)
      try:
        page.wait_for_load_state('networkidle', timeout=BROWSER_FETCH_TIMEOUT_MS)
      except PlaywrightTimeoutError:
        pass
      page.wait_for_timeout(BROWSER_WAIT_AFTER_LOAD_MS)
      extra_visual_sources = self._collect_browser_extra_visual_sources(page)
      html = page.content()
      final_url = page.url
      title = page.title()
    except Exception as exc:
      logger.warning('Browser fetch failed for %r: %s', url, exc)
      return None
    finally:
      try:
        page.close()
      except Exception:
        pass

    soup = BeautifulSoup(html, 'html.parser')
    return FetchedPage(
      requested_url=url,
      final_url=final_url,
      html=html,
      title=title,
      text=soup.get_text(' ', strip=True)[:2000],
      fetch_method='browser',
      soup=soup,
      extra_visual_sources=extra_visual_sources,
    )

  def _ensure_browser_context_worker(self):
    if self._browser_context is not None:
      return self._browser_context

    try:
      from playwright.sync_api import sync_playwright
    except Exception as exc:
      logger.warning('Playwright is unavailable: %s', exc)
      return None

    try:
      self._playwright = sync_playwright().start()
      self._browser = self._playwright.chromium.launch(headless=True)
      self._browser_context = self._browser.new_context(user_agent=self.settings.user_agent)
      return self._browser_context
    except Exception as exc:
      logger.warning('Failed to initialize Playwright browser/context: %s', exc)
      self._close_browser_worker()
      return None

  def _close_browser_worker(self) -> None:
    if self._browser_context is not None:
      try:
        self._browser_context.close()
      except Exception:
        pass
      self._browser_context = None

    if self._browser is not None:
      try:
        self._browser.close()
      except Exception:
        pass
      self._browser = None

    if self._playwright is not None:
      try:
        self._playwright.stop()
      except Exception:
        pass
      self._playwright = None

  def _collect_browser_extra_visual_sources(self, page) -> tuple[ExtraVisualSource, ...]:
    try:
      raw_sources = page.evaluate(
        '''
        (options) => {
          const maxElements = Number(options?.maxElements || 1200);
          const minCanvasSide = Number(options?.minCanvasSide || 160);
          const normalizeText = (value, maxLen = 180) => {
            if (!value) return '';
            return String(value).replace(/\\s+/g, ' ').trim().slice(0, maxLen);
          };
          const parseBackgroundUrls = (value) => {
            if (!value || value === 'none') return [];
            const urls = [];
            const regex = /url\\((['"]?)(.*?)\\1\\)/g;
            let match = regex.exec(value);
            while (match) {
              const candidate = (match[2] || '').trim();
              if (candidate) {
                urls.push(candidate);
              }
              match = regex.exec(value);
            }
            return urls;
          };
          const buildContext = (element, sourceType) => {
            const parts = [];
            const attrs = ['aria-label', 'title', 'alt', 'role'];
            for (const attr of attrs) {
              const value = element.getAttribute ? element.getAttribute(attr) : '';
              const normalized = normalizeText(value, 120);
              if (normalized) {
                parts.push(normalized);
              }
            }
            const ownText = normalizeText(element.textContent || '');
            if (ownText) {
              parts.push(ownText);
            }
            const parentText = normalizeText(element.parentElement?.textContent || '');
            if (parentText) {
              parts.push(parentText);
            }
            if (sourceType) {
              parts.push(sourceType);
            }
            return Array.from(new Set(parts)).join(' ').slice(0, 320);
          };
          const resolveUrl = (candidate) => {
            try {
              return new URL(candidate, window.location.href).href;
            } catch (error) {
              return '';
            }
          };
          const findEntryUrl = (element) => {
            const anchor = element.closest ? element.closest('a[href]') : null;
            return anchor && anchor.href ? anchor.href : null;
          };

          const results = [];
          const seen = new Set();
          const pushResult = (imageUrl, element, sourceType) => {
            if (!imageUrl) return;
            const key = `${sourceType}:${imageUrl}`;
            if (seen.has(key)) return;
            seen.add(key);
            results.push({
              image_url: imageUrl,
              context: buildContext(element, sourceType),
              entry_url: findEntryUrl(element),
              source_type: sourceType,
            });
          };

          const allElements = document.body ? document.body.querySelectorAll('*') : [];
          const inspectCount = Math.min(allElements.length, maxElements);
          for (let index = 0; index < inspectCount; index += 1) {
            const element = allElements[index];
            if (!(element instanceof Element)) continue;

            const computedStyle = window.getComputedStyle(element);
            const bgImage = computedStyle?.getPropertyValue('background-image') || '';
            for (const bgUrl of parseBackgroundUrls(bgImage)) {
              const absolute = resolveUrl(bgUrl);
              if (absolute) {
                pushResult(absolute, element, 'background-image');
              }
            }
          }

          const canvases = document.querySelectorAll('canvas');
          for (const canvas of canvases) {
            if (!(canvas instanceof HTMLCanvasElement)) continue;
            if (Math.min(canvas.width, canvas.height) < minCanvasSide) continue;
            if ((canvas.width * canvas.height) > 2_500_000) continue;
            try {
              const dataUrl = canvas.toDataURL('image/png');
              if (dataUrl && dataUrl.startsWith('data:image/')) {
                pushResult(dataUrl, canvas, 'canvas');
              }
            } catch (error) {
              // Ignore tainted canvas / blocked exports.
            }
          }

          return results;
        }
        ''',
        {
          'maxElements': MAX_BROWSER_VISUAL_SCAN_ELEMENTS,
          'minCanvasSide': 160,
        },
      )
    except Exception:
      return ()

    if not isinstance(raw_sources, list):
      return ()

    sources: list[ExtraVisualSource] = []
    seen_urls: set[tuple[str, str | None]] = set()
    for source in raw_sources:
      if not isinstance(source, dict):
        continue
      image_url = str(source.get('image_url') or '').strip()
      if not image_url:
        continue
      if image_url.startswith('blob:'):
        continue
      if image_url.startswith('data:image/') and len(image_url) > MAX_BROWSER_EXTRA_DATA_URL_CHARS:
        continue

      context = str(source.get('context') or '').strip()[:320]
      entry_url_value = str(source.get('entry_url') or '').strip()
      entry_url = entry_url_value or None
      dedupe_key = (image_url, entry_url)
      if dedupe_key in seen_urls:
        continue
      seen_urls.add(dedupe_key)

      source_type_value = str(source.get('source_type') or '').strip()
      source_type = source_type_value or None
      sources.append(
        ExtraVisualSource(
          image_url=image_url,
          context=context,
          entry_url=entry_url,
          source_type=source_type,
        ),
      )
      if len(sources) >= MAX_BROWSER_EXTRA_VISUAL_SOURCES:
        break

    return tuple(sources)

  def _collect_relevant_links(
    self,
    page: FetchedPage,
    candidate: GitHubRepositoryCandidate,
  ) -> list[str]:
    soup = page.soup or BeautifulSoup(page.html, 'html.parser')
    page_host = self._domain_key(page.final_url)
    allowed_hosts = {page_host} if page_host else set()
    candidate_repo_host = self._domain_key(candidate.repo_url)
    candidate_home_host = self._domain_key(candidate.homepage)
    if candidate_repo_host:
      allowed_hosts.add(candidate_repo_host)
    if candidate_home_host:
      allowed_hosts.add(candidate_home_host)

    scored_links: list[tuple[int, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all('a', href=True):
      href = anchor['href'].strip()
      if not href or href.startswith(('#', 'mailto:', 'javascript:')):
        continue

      absolute = urljoin(page.final_url, href)
      parsed = urlparse(absolute)
      if parsed.scheme not in {'http', 'https'}:
        continue
      if self._domain_key(absolute) not in allowed_hosts:
        continue
      if self._is_noisy_related_link(absolute):
        continue

      anchor_image_signals = self._collect_anchor_image_signals(anchor)
      joined_text = f"{anchor.get_text(' ', strip=True)} {anchor_image_signals} {absolute}".lower()
      if not any(keyword in joined_text for keyword in RELATED_PAGE_KEYWORDS):
        continue
      if self._is_low_value_related_link(absolute) and not self._has_strong_related_signal(joined_text):
        continue

      normalized = absolute.rstrip('/')
      if normalized in seen:
        continue
      seen.add(normalized)
      scored_links.append((self._score_related_link(joined_text, normalized), normalized))

    scored_links.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in scored_links[:MAX_RELATED_LINKS]]

  def _collect_anchor_image_signals(self, anchor) -> str:
    parts: list[str] = []
    for image in anchor.find_all('img')[:4]:
      for attr in ('alt', 'title', 'aria-label'):
        value = image.get(attr, '').strip()
        if value:
          parts.append(value)
      for attr in ('data-canonical-src', 'data-src', 'data-original', 'data-lazy-src', 'src'):
        value = image.get(attr, '').strip()
        if value:
          parts.append(value)
          break

    if not parts:
      return ''
    return ' '.join(dict.fromkeys(parts))

  def _score_related_link(self, joined_text: str, url: str) -> int:
    score = 0
    if 'community' in joined_text or 'discord' in joined_text or '社区' in joined_text or '社群' in joined_text:
      score += 60
    if 'support' in joined_text or 'contact' in joined_text or '支持' in joined_text or '联系' in joined_text:
      score += 35
    if (
      'join' in joined_text
      or 'group' in joined_text
      or '加入' in joined_text
      or '加群' in joined_text
      or '入群' in joined_text
      or '交流群' in joined_text
      or '讨论群' in joined_text
      or '官方群' in joined_text
    ):
      score += 30
    if (
      'wechat' in joined_text
      or 'weixin' in joined_text
      or 'qq' in joined_text
      or 'feishu' in joined_text
      or '微信' in joined_text
      or '飞书' in joined_text
    ):
      score += 25
    if (
      'wxwork' in joined_text
      or 'work wechat' in joined_text
      or 'dingtalk' in joined_text
      or 'dingding' in joined_text
      or '企业微信' in joined_text
      or '钉钉' in joined_text
    ):
      score += 25
    if '/docs' in url or 'docs.' in url or 'documentation' in joined_text:
      score -= 40
    return score

  def _has_strong_related_signal(self, joined_text: str) -> bool:
    return any(
      keyword in joined_text
      for keyword in (
        'discord',
        'qq',
        'wechat',
        'weixin',
        'feishu',
        'join',
        'invite',
        '官方群',
        '交流群',
        '加群',
        '入群',
      )
    )

  def _is_low_value_related_link(self, url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host.startswith('docs.'):
      return True
    return any(hint in path for hint in LOW_VALUE_RELATED_PATH_HINTS)

  def _is_noisy_related_link(self, url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix('www.')
    path = parsed.path.lower()
    if host in NOISY_RELATED_GITHUB_HOSTS:
      return True
    if host == 'github.com' and any(path.startswith(prefix) for prefix in NOISY_RELATED_GITHUB_PATH_PREFIXES):
      return True
    return False

  def _root_url(self, url: str | None) -> str | None:
    if not url:
      return None

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
      return None
    return f'{parsed.scheme}://{parsed.netloc}'

  def _domain_key(self, url: str | None) -> str | None:
    if not url:
      return None

    host = urlparse(url).netloc.lower().rstrip('.')
    if not host:
      return None
    if host.startswith('www.'):
      host = host[4:]

    parts = host.split('.')
    if len(parts) >= 2:
      return '.'.join(parts[-2:])
    return host

  # -------------------------------------------------------------------------
  # Result normalization
  # -------------------------------------------------------------------------

  def _dedupe_groups(self, groups: list[ExtractedGroupCandidate]) -> list[ExtractedGroupCandidate]:
    supported_platforms = {
      Platform.WECHAT,
      Platform.QQ,
      Platform.FEISHU,
      Platform.DISCORD,
      Platform.WECOM,
      Platform.DINGTALK,
    }
    deduped: dict[str, ExtractedGroupCandidate] = {}

    for group in groups:
      if group.platform not in supported_platforms:
        continue

      signature = self._group_signature(group)
      existing = deduped.get(signature)
      if existing is None:
        deduped[signature] = group
        continue

      merged_sources = list(dict.fromkeys((existing.source_urls or [existing.source_url]) + (group.source_urls or [group.source_url])))
      existing.source_urls = merged_sources
      if not existing.entry_url and group.entry_url:
        existing.entry_url = group.entry_url
      if not existing.fallback_url and group.fallback_url:
        existing.fallback_url = group.fallback_url
      if not existing.image_bytes and group.image_bytes:
        existing.image_bytes = group.image_bytes
        existing.image_content_type = group.image_content_type
        existing.image_url = group.image_url
      if not existing.decoded_payload and group.decoded_payload:
        existing.decoded_payload = group.decoded_payload
      if not existing.qq_number and group.qq_number:
        existing.qq_number = group.qq_number
      existing.qrcode_verified = existing.qrcode_verified or group.qrcode_verified

    return list(deduped.values())

  def _group_signature(self, group: ExtractedGroupCandidate) -> str:
    canonical_platform = self._canonicalize_platform(group.platform)
    if group.qq_number and group.platform == Platform.QQ:
      return f'{canonical_platform.value}:qq:{group.qq_number}'

    if group.image_bytes:
      digest = hashlib.sha1(group.image_bytes).hexdigest()
      return f'{canonical_platform.value}:image:{digest}'

    normalized_link = self._normalize_group_link(
      group.entry_url or group.fallback_url or group.decoded_payload or '',
      group.platform,
    )
    if normalized_link:
      return f'{canonical_platform.value}:link:{normalized_link}'

    fallback = group.image_url or group.source_url
    return f'{canonical_platform.value}:fallback:{fallback}'

  def _normalize_group_link(self, link: str, platform: Platform | None = None) -> str:
    if not link:
      return ''

    parsed = urlparse(link.strip())
    if not parsed.scheme or not parsed.netloc:
      return link.strip().rstrip('/').lower()

    host = parsed.netloc.lower()
    path = parsed.path.rstrip('/').lower()

    if platform == Platform.DISCORD:
      if host.endswith('discord.gg'):
        invite_code = path.strip('/').split('/', 1)[0]
        if invite_code:
          return f'https://discord.gg/{invite_code.lower()}'
      if host.endswith('discord.com') and path.startswith('/invite/'):
        invite_code = path.split('/invite/', 1)[1].split('/', 1)[0]
        if invite_code:
          return f'https://discord.com/invite/{invite_code.lower()}'
      return ''

    if platform == Platform.QQ and host in {'qm.qq.com', 'qun.qq.com', 'jq.qq.com'}:
      return urlunparse(
        (
          parsed.scheme.lower(),
          host,
          path,
          '',
          '',
          '',
        ),
      )

    if platform == Platform.FEISHU and '/share/base/form/' in parsed.path.lower():
      # Feishu business forms often differ only by prefill/hide params; keep canonical path only.
      return urlunparse(
        (
          parsed.scheme.lower(),
          host,
          path,
          '',
          '',
          '',
        ),
      )

    filtered_query = [
      (key.lower(), value)
      for key, value in parse_qsl(parsed.query, keep_blank_values=True)
      if key.lower() not in TRACKING_QUERY_KEYS
    ]
    normalized_query = '&'.join(f'{key}={value}' for key, value in sorted(filtered_query))
    normalized_path = path
    return urlunparse(
      (
        parsed.scheme.lower(),
        host,
        normalized_path,
        '',
        normalized_query,
        '',
      ),
    )

  def _is_reliable_group_link(self, link: str, platform: Platform) -> bool:
    normalized = self._normalize_group_link(link, platform)
    if not normalized:
      return False

    parsed = urlparse(normalized)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if platform == Platform.DISCORD:
      return host.endswith('discord.gg') or (host.endswith('discord.com') and path.startswith('/invite/'))
    if platform == Platform.QQ:
      return host in {'qm.qq.com', 'qun.qq.com', 'jq.qq.com'}
    if platform == Platform.FEISHU:
      return '/share/base/form/' not in path
    return True

  def _build_product_card(
    self,
    candidate: GitHubRepositoryCandidate,
    groups: list[ExtractedGroupCandidate],
  ) -> ProductCard:
    verified_at = datetime.now(timezone.utc)
    official_groups: list[OfficialGroup] = []

    for group in groups:
      if len(official_groups) >= MAX_GROUPS_PER_CARD:
        break
      raw_platform = group.platform
      canonical_platform = self._canonicalize_platform(raw_platform)

      stored_path = ''
      if group.image_bytes and group.qrcode_verified:
        stored_path = self._save_qr_code(
          group.image_bytes,
          canonical_platform,
          candidate.repo_name,
          group.image_content_type,
        )

      if raw_platform == Platform.QQ and group.qq_number:
        entry = QQNumberEntry(type='qq_number', qq_number=group.qq_number)
        group_identity = f'{canonical_platform.value}:qq:{group.qq_number}'
      elif stored_path:
        entry = QRCodeEntry(
          type='qrcode',
          image_path=stored_path,
          fallback_url=group.entry_url or group.fallback_url,
        )
        group_identity = f'{canonical_platform.value}:qrcode:{stored_path}'
      elif group.entry_url and self._is_reliable_group_link(group.entry_url, raw_platform):
        entry = LinkEntry(type='link', url=group.entry_url)
        normalized = self._normalize_group_link(group.entry_url, raw_platform) or group.entry_url
        group_identity = f'{canonical_platform.value}:link:{normalized}'
      else:
        continue

      stable_seed = f'{candidate.full_name.lower()}:{group_identity}'
      group_id = hashlib.sha1(stable_seed.encode('utf-8')).hexdigest()[:16]

      official_groups.append(
        OfficialGroup(
          group_id=group_id,
          platform=canonical_platform,
          group_type=group.group_type or GroupType.UNKNOWN,
          entry=entry,
          is_added=False,
          source_urls=group.source_urls or [group.source_url],
        ),
      )

    created_at = self._parse_datetime(candidate.created_at)
    product_id = self._candidate_product_id(candidate)

    return ProductCard(
      product_id=product_id,
      app_name=candidate.repo_name,
      description=candidate.description or '-',
      github_stars=candidate.stars,
      created_at=created_at,
      verified_at=verified_at,
      groups=official_groups,
      group_discovery_status=(
        GroupDiscoveryStatus.FOUND if official_groups else GroupDiscoveryStatus.NOT_FOUND
      ),
      official_site_url=self._root_url(candidate.homepage) or candidate.homepage,
      github_repo_url=candidate.repo_url,
    )

  def _save_qr_code(
    self,
    image_bytes: bytes,
    platform: Platform,
    repo_name: str,
    content_type: str | None,
  ) -> str:
    ext = 'png'
    if content_type:
      lowered = content_type.lower()
      if 'jpeg' in lowered or 'jpg' in lowered:
        ext = 'jpg'
      elif 'svg' in lowered:
        ext = 'svg'

    safe_name = self._safe_qrcode_name(repo_name, fallback='repo')
    safe_platform = self._safe_qrcode_name(self._canonicalize_platform(platform).value, fallback='platform')
    digest = hashlib.sha1(image_bytes).hexdigest()[:8]
    filename = f'{safe_name}_{safe_platform}_{digest}.{ext}'
    destination = self.settings.qrcode_dir / filename
    destination.write_bytes(image_bytes)
    return f'/assets/qrcodes/{filename}'

  def _parse_datetime(self, value: str | None) -> datetime | None:
    if not value:
      return None
    return datetime.fromisoformat(value.replace('Z', '+00:00'))

  # -------------------------------------------------------------------------
  # Recommendations
  # -------------------------------------------------------------------------

  def get_recommendations(self, force_refresh: bool = False) -> RecommendationsResponse:
    now = datetime.now(timezone.utc)
    if (
      not force_refresh
      and self._recommendations_cache is not None
      and (now - self._recommendations_cache[1]).total_seconds() < self._CACHE_TTL_SECONDS
    ):
      return RecommendationsResponse(
        tools=self._recommendations_cache[0],
        cached_at=self._recommendations_cache[1],
      )

    fetched_tools: list[RecommendedTool] = []
    if force_refresh or self._recommendation_pool_count() == 0:
      fetched_tools = self._fetch_recommended_tools()
      if fetched_tools:
        self._upsert_recommendation_pool(fetched_tools)

    avoid_full_names = (
      {tool.full_name for tool in self._recommendations_cache[0]}
      if force_refresh and self._recommendations_cache is not None
      else set()
    )
    tools = self._load_random_recommendations_from_pool(
      MAX_RECOMMENDATIONS,
      avoid_full_names=avoid_full_names,
    )
    if not tools and fetched_tools:
      tools = fetched_tools[:MAX_RECOMMENDATIONS]

    self._recommendations_cache = (tools, now)
    return RecommendationsResponse(tools=tools, cached_at=now)

  def _recommendation_pool_count(self) -> int:
    try:
      with get_connection(self.settings.database_path) as connection:
        row = connection.execute('SELECT COUNT(*) AS total FROM recommendation_pool').fetchone()
    except Exception:
      return 0
    return int(row['total']) if row is not None else 0

  def _upsert_recommendation_pool(self, tools: list[RecommendedTool]) -> None:
    if not tools:
      return

    now = datetime.now(timezone.utc).isoformat()
    try:
      with get_connection(self.settings.database_path) as connection:
        for tool in tools:
          connection.execute(
            '''
            INSERT INTO recommendation_pool (
              full_name,
              name,
              stars,
              description,
              topics_json,
              updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(full_name) DO UPDATE SET
              name = excluded.name,
              stars = excluded.stars,
              description = excluded.description,
              topics_json = excluded.topics_json,
              updated_at = excluded.updated_at
            ''',
            (
              tool.full_name,
              tool.name,
              tool.stars,
              tool.description,
              json.dumps(tool.topics, ensure_ascii=False),
              now,
            ),
          )

        connection.execute(
          '''
          DELETE FROM recommendation_pool
          WHERE full_name NOT IN (
            SELECT full_name
            FROM recommendation_pool
            ORDER BY stars DESC, updated_at DESC
            LIMIT ?
          )
          ''',
          (RECOMMENDATION_POOL_SIZE,),
        )
        connection.commit()
    except Exception as exc:
      logger.warning('Failed to update recommendation pool: %s', exc)

  def _load_random_recommendations_from_pool(
    self,
    limit: int,
    *,
    avoid_full_names: set[str] | None = None,
  ) -> list[RecommendedTool]:
    try:
      with get_connection(self.settings.database_path) as connection:
        rows = connection.execute(
          '''
          SELECT full_name, name, stars, description, topics_json
          FROM recommendation_pool
          ORDER BY stars DESC, updated_at DESC
          LIMIT ?
          ''',
          (RECOMMENDATION_POOL_SIZE,),
        ).fetchall()
    except Exception as exc:
      logger.warning('Failed to read recommendation pool: %s', exc)
      return []

    pool: list[RecommendedTool] = []
    for row in rows:
      try:
        topics = json.loads(row['topics_json'] or '[]')
      except Exception:
        topics = []
      pool.append(
        RecommendedTool(
          name=str(row['name']),
          full_name=str(row['full_name']),
          stars=int(row['stars']),
          description=row['description'],
          topics=topics if isinstance(topics, list) else [],
        ),
      )

    if avoid_full_names:
      filtered_pool = [tool for tool in pool if tool.full_name not in avoid_full_names]
      if len(filtered_pool) >= limit:
        pool = filtered_pool
      elif filtered_pool:
        # Keep overlap minimal while still returning enough recommendations.
        remaining = [tool for tool in pool if tool.full_name in avoid_full_names]
        pool = filtered_pool + remaining

    if not pool:
      return []
    if len(pool) <= limit:
      return pool

    sampled = random.sample(pool, limit)
    sampled.sort(key=lambda item: item.stars, reverse=True)
    return sampled

  def _fetch_recommended_tools(self) -> list[RecommendedTool]:
    query = random.choice(RECOMMENDATION_SEARCH_QUERIES)
    page = random.randint(1, 6)
    try:
      with httpx.Client(
        headers=self._github_headers(),
        follow_redirects=True,
        timeout=GITHUB_SEARCH_TIMEOUT,
      ) as client:
        response = client.get(
          'https://api.github.com/search/repositories',
          params={
            'q': query,
            'sort': 'stars',
            'order': 'desc',
            'per_page': min(100, MAX_RECOMMENDATIONS_FETCH),
            'page': page,
          },
        )
        if response.status_code in {403, 429}:
          logger.warning('GitHub API rate limited for recommendations.')
          return []
        response.raise_for_status()
    except httpx.HTTPError as exc:
      logger.warning('Failed to fetch recommendations: %s', exc)
      return []

    tools: list[RecommendedTool] = []
    for item in response.json().get('items', []):
      candidate = GitHubRepositoryCandidate(
        repo_url=item.get('html_url'),
        full_name=item.get('full_name', ''),
        repo_name=item.get('name', ''),
        owner_name=(item.get('owner') or {}).get('login', ''),
        owner_type=(item.get('owner') or {}).get('type', ''),
        homepage=self._normalize_homepage(item.get('homepage')),
        description=item.get('description'),
        stars=item.get('stargazers_count') or 0,
        topics=item.get('topics') or [],
        is_fork=bool(item.get('fork')),
        archived=bool(item.get('archived')),
        disabled=bool(item.get('disabled')),
      )
      if not candidate.repo_url or not candidate.full_name:
        continue
      if self._should_filter(candidate, None):
        continue

      tools.append(
        RecommendedTool(
          name=candidate.repo_name,
          full_name=candidate.full_name,
          stars=candidate.stars,
          description=candidate.description,
          topics=candidate.topics,
        ),
      )
      if len(tools) >= MAX_RECOMMENDATIONS_FETCH:
        break

    return tools

