export type Platform = '微信' | 'QQ' | '飞书'

export type GroupType = '交流群' | '答疑群' | '售后群' | '招募/内测群' | '未知'

export interface QRCodeEntry {
  type: 'qrcode'
  imagePath: string
  fallbackUrl?: string
}

export interface LinkEntry {
  type: 'link'
  url: string
  note: '二维码暂未抓取成功'
}

export type GroupEntry = QRCodeEntry | LinkEntry

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
}
