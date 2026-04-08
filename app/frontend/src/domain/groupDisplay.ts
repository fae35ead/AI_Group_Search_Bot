import type { GroupType, Platform } from './types'

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
