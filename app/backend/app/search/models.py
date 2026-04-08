from dataclasses import dataclass, field

from app.api.schemas import GroupType, Platform


@dataclass(frozen=True)
class NormalizedQuery:
  raw_query: str
  cleaned_query: str
  query_type: str
  domain: str | None = None
  explicit_repo_url: str | None = None


@dataclass(frozen=True)
class SearchResultLink:
  title: str
  url: str


@dataclass
class DiscoveredTargets:
  app_name: str
  official_site_url: str | None
  github_repo_url: str | None = None
  supplemental_urls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FetchedPage:
  requested_url: str
  final_url: str
  html: str
  title: str
  text: str
  fetch_method: str = 'http'


@dataclass(frozen=True)
class GitHubRepositoryMetadata:
  repo_url: str
  stars: int | None
  created_at: str | None
  description: str | None = None
  homepage: str | None = None


@dataclass(frozen=True)
class GitHubRepositoryCandidate:
  repo_url: str | None
  full_name: str
  repo_name: str
  owner_name: str
  owner_type: str
  homepage: str | None
  description: str | None
  stars: int
  topics: list[str] = field(default_factory=list)
  pushed_at: str | None = None
  updated_at: str | None = None
  created_at: str | None = None
  readme_excerpt: str | None = None
  is_fork: bool = False
  archived: bool = False
  disabled: bool = False


@dataclass(frozen=True)
class DiscoveryCandidateSummary:
  source: str
  title: str
  url: str
  score: int
  reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GitHubCandidateSummary:
  repo_url: str
  homepage: str | None
  score: int
  confident: bool
  reasons: list[str] = field(default_factory=list)


@dataclass
class DiscoveryTrace:
  official_site_url: str | None = None
  official_site_reason: str | None = None
  github_repo_url: str | None = None
  github_repo_reason: str | None = None
  web_candidates: list[DiscoveryCandidateSummary] = field(default_factory=list)
  github_candidate: GitHubCandidateSummary | None = None


@dataclass(frozen=True)
class FetchedPageSummary:
  requested_url: str
  final_url: str
  fetch_method: str


@dataclass(frozen=True)
class CandidatePageSummary:
  url: str
  score: int
  source_page: str
  source_type: str
  reasons: list[str] = field(default_factory=list)


@dataclass
class FetchTrace:
  fetched_pages: list[FetchedPageSummary] = field(default_factory=list)
  internal_links: dict[str, list[str]] = field(default_factory=dict)
  candidate_pages: list[CandidatePageSummary] = field(default_factory=list)
  site_search_queries: list[str] = field(default_factory=list)


@dataclass
class PageExtractionSummary:
  page_url: str
  scanned_tags: int = 0
  contextual_candidates: int = 0
  image_candidates: int = 0
  link_candidates: int = 0
  output_candidates: int = 0


@dataclass
class ExtractionStats:
  scanned_tags: int = 0
  contextual_candidates: int = 0
  image_candidates: int = 0
  link_candidates: int = 0
  output_candidates: int = 0
  image_download_failures: int = 0
  image_decode_successes: int = 0
  image_decode_fallbacks: int = 0
  filtered_positive_context: int = 0
  filtered_negative_context: int = 0
  filtered_platform_failure: int = 0
  filtered_qrcode_failure: int = 0
  filtered_link_noise: int = 0
  deduplicated_candidates: int = 0
  page_summaries: list[PageExtractionSummary] = field(default_factory=list)


@dataclass
class SearchTrace:
  raw_query: str
  cleaned_query: str
  query_type: str
  discovery: DiscoveryTrace = field(default_factory=DiscoveryTrace)
  fetch: FetchTrace = field(default_factory=FetchTrace)
  extraction: ExtractionStats = field(default_factory=ExtractionStats)


@dataclass
class ExtractedGroupCandidate:
  platform: Platform
  group_type: GroupType
  source_url: str
  context: str
  image_url: str | None = None
  image_bytes: bytes | None = None
  image_content_type: str | None = None
  entry_url: str | None = None
  fallback_url: str | None = None
  decoded_payload: str | None = None
  qq_number: str | None = None
  qrcode_verified: bool = False
  source_urls: list[str] = field(default_factory=list)
