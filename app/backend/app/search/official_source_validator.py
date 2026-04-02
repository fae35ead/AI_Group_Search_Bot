from urllib.parse import urlparse


class OfficialSourceValidator:
  def is_same_site(self, candidate_url: str, official_site_url: str) -> bool:
    candidate_host = self._hostname(candidate_url)
    official_host = self._hostname(official_site_url)

    if not candidate_host or not official_host:
      return False

    return (
      candidate_host == official_host
      or candidate_host.endswith(f'.{official_host}')
      or official_host.endswith(f'.{candidate_host}')
    )

  def is_same_github_repo(self, candidate_url: str, repo_url: str) -> bool:
    candidate = self._github_repo_path(candidate_url)
    target = self._github_repo_path(repo_url)
    return candidate is not None and candidate == target

  def is_official_url(
    self,
    candidate_url: str,
    official_site_url: str | None,
    github_repo_url: str | None,
  ) -> bool:
    if official_site_url and self.is_same_site(candidate_url, official_site_url):
      return True

    if github_repo_url and self.is_same_github_repo(candidate_url, github_repo_url):
      return True

    return False

  def _hostname(self, url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower().replace('www.', '')

  def _github_repo_path(self, url: str) -> str | None:
    parsed = urlparse(url)

    if parsed.netloc.lower() != 'github.com':
      return None

    segments = [segment for segment in parsed.path.split('/') if segment]

    if len(segments) < 2:
      return None

    owner, repo = segments[0], segments[1]

    if repo in {'issues', 'pulls', 'discussions', 'actions'}:
      return None

    return f'{owner}/{repo}'.lower()
