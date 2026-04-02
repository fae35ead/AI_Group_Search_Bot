import re
from urllib.parse import urlparse

from app.search.models import NormalizedQuery

DOMAIN_PATTERN = re.compile(
  r'^(?:https?://)?(?:www\.)?([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)(?:/.*)?$',
)

GITHUB_URL_PATTERN = re.compile(
  r'^(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/\s]+)(?:/.*)?$',
  re.IGNORECASE,
)


class SearchEntry:
  def normalize(self, query: str) -> NormalizedQuery:
    cleaned = self._collapse_spaces(query)

    if not cleaned:
      raise ValueError('query must not be empty')

    # Detect full GitHub repo URL first (takes priority over domain parsing)
    github_match = GITHUB_URL_PATTERN.match(cleaned)

    if github_match:
      owner, repo = github_match.group(1), github_match.group(2)
      repo = repo.rstrip('/')
      explicit_repo_url = f'https://github.com/{owner}/{repo}'
      return NormalizedQuery(
        raw_query=query,
        cleaned_query=repo,
        query_type='github_repo',
        domain=None,
        explicit_repo_url=explicit_repo_url,
      )

    domain = self._extract_domain(cleaned)
    query_type = 'domain' if domain else 'keyword'

    return NormalizedQuery(
      raw_query=query,
      cleaned_query=cleaned,
      query_type=query_type,
      domain=domain,
    )

  def _collapse_spaces(self, value: str) -> str:
    return ' '.join(value.strip().split())

  def _extract_domain(self, value: str) -> str | None:
    match = DOMAIN_PATTERN.match(value)

    if not match:
      return None

    candidate = match.group(1).lower()

    if ' ' in candidate:
      return None

    parsed = urlparse(f'https://{candidate}')
    return parsed.netloc or candidate
