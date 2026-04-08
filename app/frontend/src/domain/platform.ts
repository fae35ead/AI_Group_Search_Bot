import type { Platform } from './types'

type LegacyPlatform = Platform | '企业微信'

export function canonicalizePlatform(platform: LegacyPlatform): Platform {
  return platform === '企业微信' ? '微信' : platform
}
