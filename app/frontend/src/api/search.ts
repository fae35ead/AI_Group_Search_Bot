import type { GroupEntry, OfficialGroup, ProductCard } from '../domain/types'

interface SearchRequestDto {
  query: string
}

interface QRCodeEntryDto {
  type: 'qrcode'
  image_path: string
  fallback_url?: string | null
}

interface LinkEntryDto {
  type: 'link'
  url: string
  note: '二维码暂未抓取成功'
}

type GroupEntryDto = QRCodeEntryDto | LinkEntryDto

interface OfficialGroupDto {
  group_id: string
  platform: OfficialGroup['platform']
  group_type: OfficialGroup['groupType']
  entry: GroupEntryDto
  is_added: boolean
  source_urls: string[]
}

interface ProductCardDto {
  product_id: string
  app_name: string
  description: string
  github_stars: number | null
  created_at: string | null
  verified_at: string
  groups: OfficialGroupDto[]
}

interface SearchResponseDto {
  query: string
  results: ProductCardDto[]
  empty_message: string | null
}

export interface SearchResponse {
  query: string
  results: ProductCard[]
  emptyMessage: string | null
}

export async function searchOfficialGroups(
  query: string,
): Promise<SearchResponse> {
  const response = await fetch('/api/search', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify({
      query,
    } satisfies SearchRequestDto),
  })

  if (!response.ok) {
    throw new Error(`Search failed with status ${response.status}`)
  }

  const payload = (await response.json()) as SearchResponseDto

  return {
    query: payload.query,
    results: payload.results.map(mapProductCard),
    emptyMessage: payload.empty_message,
  }
}

function mapProductCard(card: ProductCardDto): ProductCard {
  return {
    productId: card.product_id,
    appName: card.app_name,
    description: card.description,
    githubStars: card.github_stars,
    createdAt: card.created_at,
    verifiedAt: card.verified_at,
    groups: card.groups.map(mapOfficialGroup),
  }
}

function mapOfficialGroup(group: OfficialGroupDto): OfficialGroup {
  return {
    groupId: group.group_id,
    platform: group.platform,
    groupType: group.group_type,
    entry: mapEntry(group.entry),
    isAdded: group.is_added,
    sourceUrls: group.source_urls,
  }
}

function mapEntry(entry: GroupEntryDto): GroupEntry {
  if (entry.type === 'qrcode') {
    return {
      type: 'qrcode',
      imagePath: entry.image_path,
      fallbackUrl: entry.fallback_url ?? undefined,
    }
  }

  return entry
}
