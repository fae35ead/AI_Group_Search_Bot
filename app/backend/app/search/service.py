import base64
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.api.schemas import ProductCard
from app.core.config import Settings
from app.search.entry_extractor import EntryExtractor
from app.search.models import (
  CandidatePageSummary,
  DiscoveredTargets,
  DiscoveryCandidateSummary,
  DiscoveryTrace,
  FetchTrace,
  FetchedPageSummary,
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

logger = logging.getLogger(__name__)


class SearchService:
  def __init__(self, settings: Settings):
    self.settings = settings
    self.search_entry = SearchEntry()
    self.page_fetcher = PageFetcher(settings)
    self.validator = OfficialSourceValidator()
    self.extractor = EntryExtractor(settings)
    self.normalizer = ResultNormalizer()

  def search(self, query: str) -> list[ProductCard]:
    results, trace = self.search_with_trace(query)
    self._log_trace(trace)
    return results

  def search_with_trace(self, query: str) -> tuple[list[ProductCard], SearchTrace]:
    normalized = self.search_entry.normalize(query)
    trace = SearchTrace(
      raw_query=normalized.raw_query,
      cleaned_query=normalized.cleaned_query,
      query_type=normalized.query_type,
    )
    targets = self._discover_targets(
      normalized.cleaned_query,
      normalized.domain,
      trace.discovery,
      normalized.explicit_repo_url,
    )

    if targets is None:
      return [], trace

    if targets.official_site_url is None and targets.github_repo_url is None:
      return [], trace

    pages = self._fetch_pages(targets, trace.fetch)

    if not pages:
      return [], trace

    groups = self.extractor.extract(pages, trace.extraction)

    if not groups and targets.official_site_url:
      seen_page_urls = {page.final_url for page in pages}
      fallback_pages = self._fetch_official_site_search_pages(
        targets,
        seen_page_urls,
        trace.fetch,
      )

      if fallback_pages:
        pages.extend(fallback_pages)
        groups = self.extractor.extract(fallback_pages, trace.extraction)

    github_metadata = self._fetch_github_metadata(targets.github_repo_url)
    description = self._pick_description(pages, github_metadata)

    return (
      self.normalizer.build_product_card(
        app_name=targets.app_name,
        description=description,
        github=github_metadata,
        groups=groups,
      ),
      trace,
    )

  def _log_trace(self, trace: SearchTrace) -> None:
    if not self.settings.search_debug_enabled:
      return

    logger.info(
      'search-trace %s',
      json.dumps(asdict(trace), ensure_ascii=False),
    )

  def _discover_targets(
    self,
    cleaned_query: str,
    domain: str | None,
    trace: DiscoveryTrace | None = None,
    explicit_repo_url: str | None = None,
  ) -> DiscoveredTargets | None:
    # Case: user passed a full GitHub repo URL — use it directly as github_repo_url
    if explicit_repo_url:
      app_name = cleaned_query  # repo name is already extracted by SearchEntry

      # Try to fetch github metadata to discover homepage (official site)
      github_meta = self._fetch_github_metadata(explicit_repo_url)
      official_site_url: str | None = None  # homepage discovered via GitHub API below

      if github_meta:
        # homepage field is the official site URL provided by the repo owner
        official_site_url = self._normalize_homepage(github_meta.homepage) if hasattr(github_meta, 'homepage') else None

      if trace is not None:
        trace.official_site_url = official_site_url
        trace.official_site_reason = 'from-github-repo-homepage'
        trace.github_repo_url = explicit_repo_url
        trace.github_repo_reason = 'explicit-github-repo-url'

      return DiscoveredTargets(
        app_name=app_name,
        official_site_url=official_site_url,
        github_repo_url=explicit_repo_url,
      )

    if domain:
      official_site_url = f'https://{domain}'
      app_name = self._title_from_domain(domain)
      github_candidate, github_summary = self._search_github_repository(app_name)

      if trace is not None:
        trace.official_site_url = official_site_url
        trace.official_site_reason = 'domain-input'
        trace.github_candidate = github_summary
        trace.github_repo_url = github_candidate.repo_url if github_candidate else None
        trace.github_repo_reason = (
          'trusted-github-candidate'
          if github_candidate
          else 'no-confident-github-candidate'
        )

      return DiscoveredTargets(
        app_name=app_name,
        official_site_url=official_site_url,
        github_repo_url=github_candidate.repo_url if github_candidate else None,
      )

    official_results = self._search_multi_variants(cleaned_query)
    github_candidate, github_summary = self._search_github_repository(cleaned_query)
    (
      official_site_url,
      official_site_reason,
      supplemental_urls,
      candidate_summaries,
    ) = self._select_official_site(cleaned_query, official_results, github_candidate)

    # PRD §3: GitHub is an official source — when GitHub found a candidate but
    # homepage is empty AND web search found nothing, use the repo/owner name
    # to Bing-search for the official website. This ensures we still crawl the
    # official site and have a chance at finding QR codes, not just return a
    # product card with no groups.
    if official_site_url is None and github_candidate is not None:
      bing_fallback_results = self._search_web(github_candidate.full_name)
      if bing_fallback_results:
        (
          official_site_url,
          official_site_reason,
          supplemental_urls,
          candidate_summaries,
        ) = self._select_official_site(cleaned_query, bing_fallback_results, github_candidate)
        if trace is not None:
          trace.official_site_reason = f'github-bing-fallback({official_site_reason})'

    if trace is not None:
      trace.web_candidates = candidate_summaries
      trace.github_candidate = github_summary
      trace.official_site_url = official_site_url
      trace.official_site_reason = official_site_reason
      trace.github_repo_url = github_candidate.repo_url if github_candidate else None
      trace.github_repo_reason = (
        'trusted-github-candidate'
        if github_candidate
        else 'no-confident-github-candidate'
      )

    if official_site_url is None and github_candidate is None:
      return None

    app_name = self._resolve_app_name(
      cleaned_query=cleaned_query,
      official_results=official_results,
      official_site_url=official_site_url,
      github_candidate=github_candidate,
    )

    return DiscoveredTargets(
      app_name=app_name,
      official_site_url=official_site_url,
      github_repo_url=github_candidate.repo_url if github_candidate else None,
      supplemental_urls=supplemental_urls,
    )

  def _select_official_site(
    self,
    cleaned_query: str,
    official_results: list[SearchResultLink],
    github_candidate: GitHubRepositoryCandidate | None,
  ) -> tuple[str | None, str | None, list[str], list[DiscoveryCandidateSummary]]:
    candidate_summaries: list[DiscoveryCandidateSummary] = []
    seen_urls: set[str] = set()
    supplemental_urls: list[str] = []

    for result in official_results:
      summary = self._score_official_site_candidate(
        cleaned_query,
        result,
        source='web',
        github_candidate=github_candidate,
      )
      if summary.url in seen_urls:
        continue

      seen_urls.add(summary.url)
      candidate_summaries.append(summary)

    if github_candidate and github_candidate.homepage:
      primary_homepage, extra_urls = self._normalize_homepage_candidate(
        github_candidate.homepage,
      )

      if primary_homepage:
        homepage_summary = self._score_official_site_candidate(
          cleaned_query,
          SearchResultLink(
            title=github_candidate.full_name,
            url=primary_homepage,
          ),
          source='github_homepage',
          github_candidate=github_candidate,
        )

        if homepage_summary.url not in seen_urls:
          seen_urls.add(homepage_summary.url)
          candidate_summaries.append(homepage_summary)

      supplemental_urls.extend(extra_urls)

    candidate_summaries.sort(key=self._candidate_sort_key, reverse=True)
    selected = next(
      (candidate for candidate in candidate_summaries if candidate.score >= 90),
      None,
    )

    if selected is None:
      return (
        None,
        'no-official-site-candidate-above-threshold',
        [],
        candidate_summaries,
      )

    filtered_supplemental_urls: list[str] = []

    for url in supplemental_urls:
      if url == selected.url:
        continue

      if not self.validator.is_same_site(url, selected.url):
        continue

      if url not in filtered_supplemental_urls:
        filtered_supplemental_urls.append(url)

    alternate_official_roots = [
      candidate.url
      for candidate in candidate_summaries
      if (
        candidate.url != selected.url
        and self._is_root_path(candidate.url)
        and 'brand-domain-near-exact' in candidate.reasons
      )
    ]

    for url in alternate_official_roots:
      if url not in filtered_supplemental_urls:
        filtered_supplemental_urls.append(url)

    return (
      selected.url,
      ', '.join(selected.reasons),
      filtered_supplemental_urls,
      candidate_summaries,
    )

  def _candidate_sort_key(self, candidate: DiscoveryCandidateSummary):
    return (
      candidate.score,
      int(self._is_root_path(candidate.url)),
      -self._path_depth(candidate.url),
      -len(urlparse(candidate.url).netloc),
    )

  def _fetch_pages(
    self,
    targets: DiscoveredTargets,
    trace: FetchTrace | None = None,
  ) -> list:
    fetched_pages = []
    seen: set[str] = set()
    seed_urls: list[str] = []
    max_pages = 12

    if targets.official_site_url:
      seed_urls.append(targets.official_site_url)

    for url in targets.supplemental_urls:
      if url not in seed_urls:
        seed_urls.append(url)

    for seed_url in seed_urls:
      if len(fetched_pages) >= max_pages:
        break

      page = self.page_fetcher.fetch_page(seed_url)

      if not page or page.final_url in seen:
        continue

      if (
        seed_url != targets.official_site_url
        and not self._is_allowed_official_url(page.final_url, targets)
      ):
        continue

      seen.add(page.final_url)
      fetched_pages.append(page)
      self._record_fetched_page(page, trace)

    if targets.github_repo_url:
      github_page = self.page_fetcher.fetch_page(targets.github_repo_url)

      if github_page and github_page.final_url not in seen and len(fetched_pages) < max_pages:
        fetched_pages.append(github_page)
        self._record_fetched_page(github_page, trace)
        seen.add(github_page.final_url)

        github_candidates = self._collect_candidate_pages(
          [github_page],
          targets,
          trace,
          depth_limit=2,
        )
        self._fetch_candidate_pages(
          github_candidates,
          targets,
          seen,
          trace,
          max_pages=max_pages,
          fetched_pages=fetched_pages,
        )

    first_layer_candidates = self._collect_candidate_pages(
      fetched_pages,
      targets,
      trace,
      depth_limit=8,
    )
    first_layer_pages = self._fetch_candidate_pages(
      first_layer_candidates,
      targets,
      seen,
      trace,
      max_pages=max_pages,
      fetched_pages=fetched_pages,
    )

    second_layer_candidates = self._collect_candidate_pages(
      first_layer_pages,
      targets,
      trace,
      depth_limit=4,
    )
    self._fetch_candidate_pages(
      second_layer_candidates,
      targets,
      seen,
      trace,
      max_pages=max_pages,
      fetched_pages=fetched_pages,
    )

    return fetched_pages

  def _record_fetched_page(
    self,
    page,
    trace: FetchTrace | None,
  ) -> None:
    if trace is None:
      return

    trace.fetched_pages.append(
      FetchedPageSummary(
        requested_url=page.requested_url,
        final_url=page.final_url,
        fetch_method=page.fetch_method,
      ),
    )

  def _collect_candidate_pages(
    self,
    pages: list,
    targets: DiscoveredTargets,
    trace: FetchTrace | None,
    depth_limit: int,
  ) -> list[CandidatePageSummary]:
    candidates_by_url: dict[str, CandidatePageSummary] = {}

    for page in pages:
      per_page_limit = 2 if 'github.com' in urlparse(page.final_url).netloc.lower() else depth_limit
      candidates = self.page_fetcher.discover_candidate_internal_links(
        page,
        limit=per_page_limit,
      )

      if trace is not None:
        trace.internal_links[page.final_url] = [candidate.url for candidate in candidates]
        trace.candidate_pages.extend(candidates)

      for candidate in candidates:
        if not self._is_allowed_official_url(candidate.url, targets):
          continue

        existing = candidates_by_url.get(candidate.url)
        if existing is None or candidate.score > existing.score:
          candidates_by_url[candidate.url] = candidate

    return sorted(
      candidates_by_url.values(),
      key=lambda item: (item.score, -self._path_depth(item.url)),
      reverse=True,
    )

  def _fetch_candidate_pages(
    self,
    candidates: list[CandidatePageSummary],
    targets: DiscoveredTargets,
    seen: set[str],
    trace: FetchTrace | None,
    max_pages: int,
    fetched_pages: list,
  ) -> list:
    new_pages = []

    for candidate in candidates:
      if len(fetched_pages) >= max_pages:
        break

      if candidate.url in seen:
        continue

      linked_page = self.page_fetcher.fetch_page(candidate.url)

      if not linked_page:
        continue

      if linked_page.final_url in seen:
        continue

      if not self._is_allowed_official_url(linked_page.final_url, targets):
        continue

      seen.add(linked_page.final_url)
      fetched_pages.append(linked_page)
      new_pages.append(linked_page)
      self._record_fetched_page(linked_page, trace)

      discovered_repo = self._discover_github_from_page(
        linked_page.final_url,
        linked_page.html,
      )
      if discovered_repo and targets.github_repo_url is None:
        targets.github_repo_url = discovered_repo

    return new_pages

  def _fetch_official_site_search_pages(
    self,
    targets: DiscoveredTargets,
    seen_page_urls: set[str],
    trace: FetchTrace | None,
  ) -> list:
    if not targets.official_site_url:
      return []

    fallback_candidates: dict[str, CandidatePageSummary] = {}
    fetched_pages = []
    candidate_hosts = {
      urlparse(targets.official_site_url).netloc.lower(),
      *[urlparse(url).netloc.lower() for url in targets.supplemental_urls],
    }

    for host in sorted(filter(None, candidate_hosts)):
      for query in self._build_site_search_queries(targets, host):
        if trace is not None:
          trace.site_search_queries.append(query)

        for result in self._search_web(query):
          if len(fetched_pages) >= 3:
            return fetched_pages

          if not self._is_allowed_official_url(result.url, targets):
            continue

          if result.url in seen_page_urls:
            continue

          score = self._score_site_search_result(
            result,
            targets.app_name,
            targets.official_site_url,
          )
          candidate = CandidatePageSummary(
            url=result.url,
            score=score,
            source_page=query,
            source_type='site_search',
            reasons=['site-search'],
          )
          existing = fallback_candidates.get(result.url)
          if existing is None or candidate.score > existing.score:
            fallback_candidates[result.url] = candidate

    ordered_candidates = sorted(
      fallback_candidates.values(),
      key=lambda item: (item.score, -self._path_depth(item.url)),
      reverse=True,
    )

    if trace is not None:
      trace.candidate_pages.extend(ordered_candidates)

    for candidate in ordered_candidates[:3]:
      page = self.page_fetcher.fetch_page(candidate.url)
      if not page or page.final_url in seen_page_urls:
        continue
      fetched_pages.append(page)
      seen_page_urls.add(page.final_url)
      self._record_fetched_page(page, trace)

    return fetched_pages

  def _build_site_search_queries(
    self,
    targets: DiscoveredTargets,
    host: str,
  ) -> list[str]:
    host = host.removeprefix('www.')
    brand = targets.app_name or self._title_from_domain(host)
    return [
      f'site:{host} {brand} 飞书',
      f'site:{host} {brand} 开发者 社区',
      f'site:{host} {brand} 二维码',
      f'site:{host} {brand} 社群',
    ]

  def _score_site_search_result(
    self,
    result: SearchResultLink,
    app_name: str,
    official_site_url: str,
  ) -> int:
    summary = self._score_official_site_candidate(
      app_name,
      result,
      source='site_search',
      github_candidate=None,
    )
    return summary.score + 40

  def _search_web(self, query: str) -> list[SearchResultLink]:
    results = self._search_bing(query)

    if results:
      return results

    return self._search_duckduckgo(query)

  def _search_multi_variants(self, query: str) -> list[SearchResultLink]:
    """Search with multiple query variants to improve official-site discovery rate."""
    variants = [
      f'{query} official site',
      f'{query} 官方群',
      f'{query} 官网',
      f'{query} github',
    ]

    all_results: list[SearchResultLink] = []
    seen: set[str] = set()

    for variant in variants:
      results = self._search_web(variant)

      for result in results:
        if result.url not in seen:
          seen.add(result.url)
          all_results.append(result)

      if len(all_results) >= 15:
        break

    return all_results

  def _search_bing(self, query: str) -> list[SearchResultLink]:
    try:
      with httpx.Client(
        headers={'User-Agent': self.settings.user_agent},
        follow_redirects=True,
        timeout=self.settings.request_timeout_seconds,
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
      target_url = self._resolve_search_result_url(
        (item.findtext('link') or '').strip(),
      )

      if not title or not target_url:
        continue

      results.append(
        SearchResultLink(
          title=title,
          url=target_url,
        ),
      )

      if len(results) >= 8:
        break

    return results

  def _search_duckduckgo(self, query: str) -> list[SearchResultLink]:
    try:
      with httpx.Client(
        headers={'User-Agent': self.settings.user_agent},
        follow_redirects=True,
        timeout=self.settings.request_timeout_seconds,
      ) as client:
        response = client.get(
          'https://duckduckgo.com/html/',
          params={'q': query},
        )
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

      results.append(
        SearchResultLink(
          title=anchor.get_text(' ', strip=True),
          url=target_url,
        ),
      )

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
    except (ValueError, UnicodeDecodeError):
      return ''

    return decoded if decoded.startswith(('http://', 'https://')) else ''

  def _search_github_repository(
    self,
    query: str,
  ) -> tuple[GitHubRepositoryCandidate | None, GitHubCandidateSummary | None]:
    try:
      with httpx.Client(
        headers={
          'User-Agent': self.settings.user_agent,
          'Accept': 'application/vnd.github+json',
        },
        follow_redirects=True,
        timeout=self.settings.request_timeout_seconds,
      ) as client:
        # Search across name, description, and topics to maximize recall
        # PRD §3: GitHub is an official source; broad search ensures product cards
        # are returned even when repo name doesn't exactly match the query
        search_queries = [
          f'{query} in:name',
          f'{query} in:description',
          f'{query} in:topics',
        ]

        all_items: list[dict] = []
        seen_full_names: set[str] = set()

        for q in search_queries:
          q_response = client.get(
            'https://api.github.com/search/repositories',
            params={
              'q': q,
              'per_page': 10,
              'sort': 'stars',
              'order': 'desc',
            },
          )
          try:
            q_response.raise_for_status()
          except httpx.HTTPError:
            continue

          for item in q_response.json().get('items', []):
            full_name = item.get('full_name', '')
            if full_name and full_name not in seen_full_names:
              seen_full_names.add(full_name)
              all_items.append(item)

          if len(all_items) >= 15:
            break
    except httpx.HTTPError:
      return None, None

    if not all_items:
      return None, None

    best_candidate: GitHubRepositoryCandidate | None = None
    best_score = -999
    best_reasons: list[str] = []

    for item in all_items:
      repo_url = item.get('html_url')
      full_name = item.get('full_name')
      owner = item.get('owner') or {}

      if not repo_url or not full_name:
        continue

      candidate = GitHubRepositoryCandidate(
        repo_url=repo_url,
        full_name=full_name,
        repo_name=item.get('name', ''),
        owner_name=owner.get('login', ''),
        owner_type=owner.get('type', ''),
        homepage=self._normalize_homepage(item.get('homepage')),
        description=item.get('description'),
        stars=item.get('stargazers_count') or 0,
      )
      score, reasons = self._score_github_repository(query, candidate)

      if score > best_score:
        best_candidate = candidate
        best_score = score
        best_reasons = reasons

    if best_candidate is None:
      return None, None

    confident, confidence_reasons = self._is_confident_github_candidate(
      query,
      best_candidate,
      best_score,
    )
    summary = GitHubCandidateSummary(
      repo_url=best_candidate.repo_url,
      homepage=best_candidate.homepage,
      score=best_score,
      confident=confident,
      reasons=best_reasons + confidence_reasons,
    )

    return (best_candidate if confident else None), summary

  def _score_github_repository(
    self,
    query: str,
    candidate: GitHubRepositoryCandidate,
  ) -> tuple[int, list[str]]:
    query_tokens = self._tokenize(query)
    query_joined = ''.join(query_tokens)
    repo_tokens = self._tokenize(candidate.repo_name)
    owner_tokens = self._tokenize(candidate.owner_name)
    homepage_tokens = self._tokenize(candidate.homepage or '')
    homepage_brand_tokens = self._tokenize(
      self._brand_component(urlparse(candidate.homepage or '').netloc.lower()),
    )
    score = min(candidate.stars // 1000, 60)
    reasons = ['stars-signal']

    if query_joined and repo_tokens and ''.join(repo_tokens) == query_joined:
      score += 120
      reasons.append('repo-name-exact')
    elif query_tokens and all(token in repo_tokens for token in query_tokens):
      score += 70
      reasons.append('repo-token-match')
    elif query_joined and query_joined in ''.join(repo_tokens):
      score += 35
      reasons.append('repo-substring-match')
      if ''.join(repo_tokens) != query_joined:
        score -= 50
        reasons.append('repo-substring-only-penalty')

    if query_tokens and any(token in owner_tokens for token in query_tokens):
      score += 20
      reasons.append('owner-match')

    if candidate.owner_type.lower() == 'organization':
      score += 20
      reasons.append('organization-owner')

    if candidate.homepage:
      score += 20
      reasons.append('homepage-present')

      if query_tokens and all(token in homepage_brand_tokens for token in query_tokens):
        score += 40
        reasons.append('homepage-brand-match')
      elif query_joined and query_joined in ''.join(homepage_tokens):
        score += 15
        reasons.append('homepage-substring-match')

    noisy_tokens = {
      'awesome',
      'clone',
      'clones',
      'guide',
      'guides',
      'learn',
      'tutorial',
      'free',
      'auto',
      'help',
      'rules',
      'for',
      'beginners',
      'starter',
      'examples',
      'demo',
      'docs',
      'doc',
      'tool',
      'tools',
      'sdk',
    }

    if any(token in noisy_tokens for token in repo_tokens):
      score -= 60
      reasons.append('noisy-repo-token-penalty')

    if candidate.description:
      description_tokens = self._tokenize(candidate.description)
      if query_tokens and any(token in description_tokens for token in query_tokens):
        score += 10
        reasons.append('description-match')

    return score, reasons

  def _is_confident_github_candidate(
    self,
    query: str,
    candidate: GitHubRepositoryCandidate,
    score: int,
  ) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if score < 80:
      reasons.append('score-below-confidence-threshold')
      return False, reasons

    query_tokens = self._tokenize(query)
    repo_tokens = self._tokenize(candidate.repo_name)
    owner_tokens = self._tokenize(candidate.owner_name)
    homepage_brand_tokens = self._tokenize(
      self._brand_component(urlparse(candidate.homepage or '').netloc.lower()),
    )
    exact_repo = query_tokens and ''.join(repo_tokens) == ''.join(query_tokens)
    repo_contains_all = query_tokens and all(token in repo_tokens for token in query_tokens)
    repo_extra_tokens = [token for token in repo_tokens if token not in query_tokens]
    allowed_extra_tokens = {'code', 'app', 'ai', 'sdk', 'cli'}
    repo_extra_allowed = all(token in allowed_extra_tokens for token in repo_extra_tokens)
    organization = candidate.owner_type.lower() == 'organization'
    owner_match = query_tokens and any(token in owner_tokens for token in query_tokens)
    homepage_match = (
      bool(query_tokens)
      and bool(homepage_brand_tokens)
      and all(token in homepage_brand_tokens for token in query_tokens)
    )

    if exact_repo and (organization or owner_match or homepage_match):
      reasons.append('exact-repo-with-official-signal')
      return True, reasons

    if repo_contains_all and repo_extra_allowed and homepage_match:
      reasons.append('repo-token-match-with-homepage-signal')
      return True, reasons

    if repo_contains_all and repo_extra_allowed and organization and owner_match:
      reasons.append('repo-token-match-with-owner-signal')
      return True, reasons

    reasons.append('missing-official-signal')
    return False, reasons

  def _score_official_site_candidate(
    self,
    query: str,
    result: SearchResultLink,
    source: str,
    github_candidate: GitHubRepositoryCandidate | None,
  ) -> DiscoveryCandidateSummary:
    parsed = urlparse(result.url)
    host = parsed.netloc.lower().replace('www.', '')
    title = result.title.strip()
    path = parsed.path or '/'
    lowered_title = title.lower()
    reasons: list[str] = []
    score = 0

    blocked_hosts = {
      'youtube.com',
      'x.com',
      'twitter.com',
      'linkedin.com',
      'reddit.com',
      'github.com',
      'huggingface.co',
      'discord.com',
      'discord.gg',
      'telegram.org',
      't.me',
    }

    if any(host == blocked or host.endswith(f'.{blocked}') for blocked in blocked_hosts):
      return DiscoveryCandidateSummary(
        source=source,
        title=title,
        url=result.url,
        score=-500,
        reasons=['blocked-host'],
      )

    query_tokens = self._tokenize(query)
    query_joined = ''.join(query_tokens)
    brand_component = self._brand_component(host)
    brand_tokens = self._tokenize(brand_component)
    title_tokens = self._tokenize(title)
    path_tokens = self._tokenize(path)

    if query_joined and brand_component == query_joined:
      score += 140
      reasons.append('brand-domain-exact')
    elif query_joined and self._is_near_brand_match(brand_component, query_joined):
      score += 95
      reasons.append('brand-domain-near-exact')
    elif query_tokens and all(token in brand_tokens for token in query_tokens):
      score += 90
      reasons.append('brand-domain-token-match')
      extra_brand_tokens = [token for token in brand_tokens if token not in query_tokens]

      if extra_brand_tokens:
        generic_extra_tokens = {'ai', 'app', 'hq', 'labs', 'lab', 'cloud'}

        if all(token in generic_extra_tokens for token in extra_brand_tokens):
          score -= 20
          reasons.append('brand-domain-generic-extra')
        else:
          score -= 95
          reasons.append('brand-domain-extra-token-penalty')
    elif query_joined and query_joined in ''.join(brand_tokens):
      score += 25
      reasons.append('brand-domain-substring-match')

    if self._is_root_path(result.url):
      score += 30
      reasons.append('root-path')
    else:
      if any(
        token in {
          'community',
          'support',
          'contact',
          'join',
          'invite',
          'forum',
        }
        for token in path_tokens
      ):
        score += 15
        reasons.append('official-subpage')

      if any(
        token in {
          'docs',
          'doc',
          'blog',
          'news',
          'pricing',
          'download',
          'downloads',
          'article',
          'articles',
          'changelog',
        }
        for token in path_tokens
      ):
        score -= 70
        reasons.append('non-homepage-path')

    if len([label for label in host.split('.') if label and label != 'www']) > 2:
      score -= 20
      reasons.append('deep-subdomain')

    if query_tokens and title_tokens[: len(query_tokens)] == query_tokens:
      score += 35
      reasons.append('title-prefix-match')
    elif query_tokens and all(token in title_tokens for token in query_tokens):
      score += 20
      reasons.append('title-token-match')

    if any(
      marker in lowered_title
      for marker in ('what is', 'tutorial', 'guide', 'review', 'pricing', 'download')
    ) or any(
      marker in title
      for marker in ('是什麼', '教學', '介紹', '攻略', '雜誌')
    ):
      score -= 80
      reasons.append('article-like-title')

    if source == 'github_homepage':
      score += 10
      reasons.append('github-homepage-signal')

    if (
      github_candidate
      and github_candidate.homepage
      and self.validator.is_same_site(result.url, github_candidate.homepage)
    ):
      score += 25
      reasons.append('matches-github-homepage-site')

    return DiscoveryCandidateSummary(
      source=source,
      title=title,
      url=result.url,
      score=score,
      reasons=reasons,
    )

  def _normalize_homepage_candidate(
    self,
    homepage: str,
  ) -> tuple[str | None, list[str]]:
    normalized = self._normalize_homepage(homepage)

    if not normalized:
      return None, []

    parsed = urlparse(normalized)
    root_url = f'{parsed.scheme}://{parsed.netloc}'

    if parsed.path and parsed.path not in {'', '/'}:
      return root_url, [normalized]

    return normalized, []

  def _normalize_homepage(self, homepage: str | None) -> str | None:
    if not homepage:
      return None

    homepage = homepage.strip()

    if not homepage.startswith(('http://', 'https://')):
      return None

    host = urlparse(homepage).netloc.lower()

    if host in {'github.com', 'www.github.com'}:
      return None

    return homepage.rstrip('/')

  def _tokenize(self, value: str) -> list[str]:
    prepared = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', value)
    return re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]+', prepared.lower())

  def _brand_component(self, host: str) -> str:
    labels = [label for label in host.split('.') if label and label != 'www']

    if not labels:
      return ''

    if host.endswith('.github.io') and len(labels) >= 3:
      return labels[-3]

    if len(labels) >= 2:
      return labels[-2]

    return labels[0]

  def _is_near_brand_match(self, brand_component: str, query_joined: str) -> bool:
    if not brand_component or not query_joined:
      return False

    if brand_component == query_joined:
      return False

    if brand_component.startswith(query_joined) and len(brand_component) - len(query_joined) <= 1:
      return True

    if query_joined.startswith(brand_component) and len(query_joined) - len(brand_component) <= 1:
      return True

    return False

  def _is_root_path(self, url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path in {'', '/'}

  def _path_depth(self, url: str) -> int:
    parsed = urlparse(url)
    return len([segment for segment in parsed.path.split('/') if segment])

  def _is_allowed_official_url(self, url: str, targets: DiscoveredTargets) -> bool:
    if self.validator.is_official_url(
      url,
      targets.official_site_url,
      targets.github_repo_url,
    ):
      return True

    return any(
      self.validator.is_same_site(url, supplemental_url)
      for supplemental_url in targets.supplemental_urls
    )

  def _resolve_app_name(
    self,
    cleaned_query: str,
    official_results: list[SearchResultLink],
    official_site_url: str | None,
    github_candidate: GitHubRepositoryCandidate | None,
  ) -> str:
    if github_candidate:
      repo_tokens = self._tokenize(github_candidate.repo_name)
      query_tokens = self._tokenize(cleaned_query)

      if query_tokens and all(token in repo_tokens for token in query_tokens):
        return cleaned_query

    if official_site_url:
      titled = self._title_from_url(official_results, official_site_url, cleaned_query)

      if titled and self._title_matches_query(titled, cleaned_query):
        return titled

    return cleaned_query

  def _title_matches_query(self, title: str, query: str) -> bool:
    title_tokens = self._tokenize(title)
    query_tokens = self._tokenize(query)

    if not title_tokens or not query_tokens:
      return False

    return all(token in title_tokens for token in query_tokens)

  def _discover_github_from_page(self, page_url: str, html: str) -> str | None:
    soup = BeautifulSoup(html, 'html.parser')

    for anchor in soup.find_all('a', href=True):
      href = urljoin(page_url, anchor['href'])
      repo_url = self._normalize_github_repo_url(href)

      if repo_url:
        return repo_url

    return None

  def _normalize_github_repo_url(self, url: str) -> str | None:
    parsed = urlparse(url)

    if parsed.netloc.lower() != 'github.com':
      return None

    segments = [segment for segment in parsed.path.split('/') if segment]

    if len(segments) < 2:
      return None

    owner, repo = segments[0], segments[1]
    repo = repo.removesuffix('.git')

    blocked = {'issues', 'pulls', 'discussions', 'actions', 'marketplace'}

    if owner in blocked or repo in blocked:
      return None

    return f'https://github.com/{owner}/{repo}'

  def _fetch_github_metadata(self, repo_url: str | None) -> GitHubRepositoryMetadata | None:
    if repo_url is None:
      return None

    parsed = urlparse(repo_url)
    segments = [segment for segment in parsed.path.split('/') if segment]

    if len(segments) < 2:
      return None

    owner, repo = segments[0], segments[1]

    try:
      with httpx.Client(
        headers={
          'User-Agent': self.settings.user_agent,
          'Accept': 'application/vnd.github+json',
        },
        follow_redirects=True,
        timeout=self.settings.request_timeout_seconds,
      ) as client:
        response = client.get(f'https://api.github.com/repos/{owner}/{repo}')
        response.raise_for_status()
    except httpx.HTTPError:
      return None

    payload = response.json()

    return GitHubRepositoryMetadata(
      repo_url=repo_url,
      stars=payload.get('stargazers_count'),
      created_at=payload.get('created_at'),
      description=payload.get('description'),
      homepage=payload.get('homepage'),
    )

  def _pick_description(
    self,
    pages: list,
    github_metadata: GitHubRepositoryMetadata | None,
  ) -> str:
    for page in pages:
      soup = BeautifulSoup(page.html, 'html.parser')

      for selector in (
        'meta[property="og:description"]',
        'meta[name="description"]',
      ):
        node = soup.select_one(selector)
        content = node.get('content', '').strip() if node else ''

        if content:
          return content

    if github_metadata and github_metadata.description:
      return github_metadata.description

    return '—'

  def _title_from_url(
    self,
    results: list[SearchResultLink],
    official_site_url: str,
    fallback_query: str,
  ) -> str:
    for result in results:
      if result.url == official_site_url:
        title = result.title.split(' - ')[0].split(':')[0].strip()
        if title:
          return title

    return fallback_query

  def _title_from_domain(self, domain: str) -> str:
    host = domain.split('.')[0]
    host = re.sub(r'[-_]+', ' ', host)
    return host.strip().title() or domain
