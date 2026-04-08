export type Platform =
  | '\u5fae\u4fe1'
  | 'QQ'
  | '\u98de\u4e66'
  | 'Discord'
  | '\u4f01\u4e1a\u5fae\u4fe1'
  | '\u9489\u9489'

export type GroupType =
  | '\u4ea4\u6d41\u7fa4'
  | '\u7b54\u7591\u7fa4'
  | '\u552e\u540e\u7fa4'
  | '\u62db\u52df/\u5185\u6d4b\u7fa4'
  | '\u672a\u77e5'

export type GroupDiscoveryStatus = 'found' | 'not_found'

export interface QRCodeEntry {
  type: 'qrcode'
  imagePath: string
  fallbackUrl?: string
}

export interface LinkEntry {
  type: 'link'
  url: string
  note: '\u4e8c\u7ef4\u7801\u6682\u672a\u6293\u53d6\u6210\u529f'
}

export interface QQNumberEntry {
  type: 'qq_number'
  qqNumber: string
  note: '未发现二维码/链接，已提取QQ群号'
}

export type GroupEntry = QRCodeEntry | LinkEntry | QQNumberEntry

export interface OfficialGroup {
  groupId: string
  platform: Platform
  groupType: GroupType
  entry: GroupEntry
  isAdded: boolean
  sourceUrls: string[]
}

export interface ProductCard {
  productId: string
  appName: string
  description: string
  githubStars: number | null
  createdAt: string | null
  verifiedAt: string
  groups: OfficialGroup[]
  groupDiscoveryStatus: GroupDiscoveryStatus
  officialSiteUrl?: string
  githubRepoUrl?: string
}

export interface SearchFilters {
  minStars?: number
  createdAfter?: string
  createdBefore?: string
}

export interface RecommendedTool {
  name: string
  fullName: string
  stars: number
  description: string | null
  topics: string[]
}

export interface ViewedGroup {
  viewKey: string
  productId: string
  appName: string
  platform: Platform
  groupType: GroupType
  entry: GroupEntry
  viewedAt: string
}

export type ManualEntryType = 'qrcode' | 'link'

export interface ManualUploadInput {
  appName: string
  description?: string
  createdAt?: string
  githubStars?: number
  platform: Platform
  groupType: GroupType
  entryType: ManualEntryType
  entryUrl?: string
  fallbackUrl?: string
  qrcodeFile?: File
}
