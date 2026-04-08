import type {
  GroupEntry,
  GroupDiscoveryStatus,
  GroupType,
  ManualUploadInput,
  OfficialGroup,
  Platform,
  ProductCard,
  RecommendedTool,
  SearchFilters,
  ViewedGroup,
} from '../domain/types'

interface SearchRequestDto {
  query: string
  filters?: {
    min_stars?: number
    created_after?: string
    created_before?: string
  }
  refresh?: boolean
  limit?: number
}

interface QRCodeEntryDto {
  type: 'qrcode'
  image_path: string
  fallback_url?: string | null
}

interface LinkEntryDto {
  type: 'link'
  url: string
  note: '\u4e8c\u7ef4\u7801\u6682\u672a\u6293\u53d6\u6210\u529f'
}

interface QQNumberEntryDto {
  type: 'qq_number'
  qq_number: string
  note: '未发现二维码/链接，已提取QQ群号'
}

type GroupEntryDto = QRCodeEntryDto | LinkEntryDto | QQNumberEntryDto

interface OfficialGroupDto {
  group_id: string
  platform: Platform
  group_type: GroupType
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
  group_discovery_status: GroupDiscoveryStatus
  official_site_url?: string | null
  github_repo_url?: string | null
}

interface SearchResponseDto {
  query: string
  results: ProductCardDto[]
  empty_message: string | null
}

interface ViewedGroupDto {
  view_key: string
  product_id: string
  app_name: string
  platform: Platform
  group_type: GroupType
  entry: GroupEntryDto
  viewed_at: string
}

interface ViewedGroupsResponseDto {
  groups: ViewedGroupDto[]
}

interface MarkViewedGroupRequestDto {
  product_id: string
  app_name: string
  group: OfficialGroupDto
}

interface RecommendedToolDto {
  name: string
  full_name: string
  stars: number
  description: string | null
  topics: string[]
}

interface RecommendationsResponseDto {
  tools: RecommendedToolDto[]
  cached_at: string
}

interface ManualUploadResponseDto {
  ok: true
  view_key: string
}

export interface ManualUploadResponse {
  ok: true
  viewKey: string
}

export interface RecommendationsResponse {
  tools: RecommendedTool[]
  cachedAt: string
}

export async function fetchRecommendations(forceRefresh = false): Promise<RecommendationsResponse> {
  const suffix = forceRefresh ? '?refresh=true' : ''
  const response = await fetch(`/api/recommendations${suffix}`)

  if (!response.ok) {
    throw new Error(`Recommendations failed with status ${response.status}`)
  }

  const payload = (await response.json()) as RecommendationsResponseDto

  return {
    tools: payload.tools.map(
      (tool): RecommendedTool => ({
        name: tool.name,
        fullName: tool.full_name,
        stars: tool.stars,
        description: tool.description,
        topics: tool.topics,
      }),
    ),
    cachedAt: payload.cached_at,
  }
}

export interface SearchResponse {
  query: string
  results: ProductCard[]
  emptyMessage: string | null
}

interface SearchOptions {
  refresh?: boolean
  signal?: AbortSignal
  limit?: number
}

export async function searchOfficialGroups(
  query: string,
  filters?: SearchFilters,
  options?: SearchOptions,
): Promise<SearchResponse> {
  const response = await fetch('/api/search', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    signal: options?.signal,
    body: JSON.stringify({
      query,
      filters: mapFilters(filters),
      refresh: options?.refresh ?? false,
      limit: options?.limit ?? 10,
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

export async function fetchViewedGroups(): Promise<ViewedGroup[]> {
  const response = await fetch('/api/groups/viewed')
  if (!response.ok) {
    throw new Error(`Fetch viewed groups failed with status ${response.status}`)
  }

  const payload = (await response.json()) as ViewedGroupsResponseDto
  return payload.groups.map((group): ViewedGroup => ({
    viewKey: group.view_key,
    productId: group.product_id,
    appName: group.app_name,
    platform: group.platform,
    groupType: group.group_type,
    entry: mapEntry(group.entry),
    viewedAt: group.viewed_at,
  }))
}

export async function markGroupViewed(product: ProductCard, group: OfficialGroup): Promise<void> {
  const requestPayload: MarkViewedGroupRequestDto = {
    product_id: product.productId,
    app_name: product.appName,
    group: {
      group_id: group.groupId,
      platform: group.platform,
      group_type: group.groupType,
      entry: mapEntryToDto(group.entry),
      is_added: group.isAdded,
      source_urls: group.sourceUrls,
    },
  }

  const response = await fetch('/api/groups/viewed', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(requestPayload),
  })
  if (!response.ok) {
    throw new Error(`Mark viewed failed with status ${response.status}`)
  }
}

export async function removeViewedGroup(viewKey: string): Promise<void> {
  const response = await fetch(`/api/groups/viewed/${encodeURIComponent(viewKey)}`, {
    method: 'DELETE',
    headers: {
      Accept: 'application/json',
    },
  })
  if (!response.ok) {
    throw new Error(`Remove viewed failed with status ${response.status}`)
  }
}

export async function manualUploadGroup(input: ManualUploadInput): Promise<ManualUploadResponse> {
  const formData = new FormData()
  formData.append('app_name', input.appName)
  formData.append('platform', input.platform)
  formData.append('group_type', input.groupType)
  formData.append('entry_type', input.entryType)

  if (input.description) {
    formData.append('description', input.description)
  }
  if (input.createdAt) {
    formData.append('created_at', input.createdAt)
  }
  if (typeof input.githubStars === 'number' && !Number.isNaN(input.githubStars)) {
    formData.append('github_stars', String(input.githubStars))
  }
  if (input.entryUrl) {
    formData.append('entry_url', input.entryUrl)
  }
  if (input.fallbackUrl) {
    formData.append('fallback_url', input.fallbackUrl)
  }
  if (input.entryType === 'qrcode' && input.qrcodeFile) {
    formData.append('qrcode_file', input.qrcodeFile)
  }

  const response = await fetch('/api/groups/manual-upload', {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    throw new Error(`Manual upload failed with status ${response.status}`)
  }

  const payload = (await response.json()) as ManualUploadResponseDto
  return { ok: payload.ok, viewKey: payload.view_key }
}

function mapFilters(filters?: SearchFilters): SearchRequestDto['filters'] {
  if (!filters) {
    return undefined
  }

  const mapped: NonNullable<SearchRequestDto['filters']> = {}
  if (typeof filters.minStars === 'number') {
    mapped.min_stars = filters.minStars
  }
  if (filters.createdAfter) {
    mapped.created_after = new Date(`${filters.createdAfter}T00:00:00Z`).toISOString()
  }
  if (filters.createdBefore) {
    mapped.created_before = new Date(`${filters.createdBefore}T23:59:59.999Z`).toISOString()
  }

  return Object.keys(mapped).length > 0 ? mapped : undefined
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
    groupDiscoveryStatus: card.group_discovery_status,
    officialSiteUrl: card.official_site_url ?? undefined,
    githubRepoUrl: card.github_repo_url ?? undefined,
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

function mapEntryToDto(entry: GroupEntry): GroupEntryDto {
  if (entry.type === 'qrcode') {
    return {
      type: 'qrcode',
      image_path: entry.imagePath,
      fallback_url: entry.fallbackUrl ?? null,
    }
  }

  if (entry.type === 'qq_number') {
    return {
      type: 'qq_number',
      qq_number: entry.qqNumber,
      note: entry.note,
    }
  }

  return {
    type: 'link',
    url: entry.url,
    note: entry.note,
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

  if (entry.type === 'qq_number') {
    return {
      type: 'qq_number',
      qqNumber: entry.qq_number,
      note: entry.note,
    }
  }

  return {
    type: 'link',
    url: entry.url,
    note: entry.note,
  }
}
