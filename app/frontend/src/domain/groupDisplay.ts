import type { GroupType, Platform, ViewedGroup } from './types'

const UNKNOWN_GROUP_TYPE: GroupType = '未知'

export function isUnknownGroupType(groupType: GroupType): boolean {
  return groupType === UNKNOWN_GROUP_TYPE
}

export function formatGroupLabel(platform: Platform, groupType: GroupType): string {
  if (isUnknownGroupType(groupType)) {
    return platform
  }

  return `${platform} · ${groupType}`
}

export function describeViewedEntry(group: ViewedGroup): string {
  if (group.entry.type === 'qrcode') {
    return group.entry.fallbackUrl ? '二维码入口，可直接打开链接' : '二维码入口，支持扫码加入'
  }
  if (group.entry.type === 'qq_number') {
    return `QQ群号：${group.entry.qqNumber}`
  }
  return group.entry.note
}

