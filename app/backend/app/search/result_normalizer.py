import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from app.api.schemas import LinkEntry, OfficialGroup, ProductCard, QRCodeEntry
from app.search.models import ExtractedGroupCandidate, GitHubRepositoryMetadata


class ResultNormalizer:
  def build_product_card(
    self,
    app_name: str,
    description: str,
    github: GitHubRepositoryMetadata | None,
    groups: list[ExtractedGroupCandidate],
  ) -> list[ProductCard]:
    if not groups:
      return []

    verified_at = datetime.now(timezone.utc)
    normalized_groups: list[OfficialGroup] = []

    for group in groups:
      entry = self._build_entry(group)
      stable_id = self._stable_id(group)

      normalized_groups.append(
        OfficialGroup(
          group_id=stable_id,
          platform=group.platform,
          group_type=group.group_type,
          entry=entry,
          is_added=False,
          source_urls=group.source_urls or [group.source_url],
        ),
      )

    normalized_groups.sort(key=lambda item: (item.platform.value, item.group_type.value))
    product_id = self._slugify(app_name)

    return [
      ProductCard(
        product_id=product_id,
        app_name=app_name,
        description=description or '—',
        github_stars=github.stars if github else None,
        created_at=self._parse_datetime(github.created_at if github else None),
        verified_at=verified_at,
        groups=normalized_groups,
      ),
    ]

  def _build_entry(self, group: ExtractedGroupCandidate) -> QRCodeEntry | LinkEntry:
    if group.image_url:
      return QRCodeEntry(
        type='qrcode',
        image_path=group.image_url,
        fallback_url=group.fallback_url or group.entry_url,
      )

    return LinkEntry(
      type='link',
      url=group.entry_url or group.fallback_url or group.source_url,
    )

  def _stable_id(self, group: ExtractedGroupCandidate) -> str:
    raw = '|'.join(
      filter(
        None,
        [
          group.platform.value,
          group.group_type.value,
          group.image_url,
          group.entry_url,
          group.source_url,
        ],
      ),
    )
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]

  def _slugify(self, app_name: str) -> str:
    return hashlib.sha1(app_name.encode('utf-8')).hexdigest()[:12]

  def _parse_datetime(self, value: str | None):
    if not value:
      return None

    return datetime.fromisoformat(value.replace('Z', '+00:00'))
