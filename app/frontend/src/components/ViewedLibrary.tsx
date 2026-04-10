import { useMemo, useState } from 'react'

import type { GroupType, Platform, ViewedGroup } from '../domain/types'

const UNKNOWN_GROUP_TYPE: GroupType = '\u672a\u77e5'
const PAGE_SIZE = 12

const PLATFORM_OPTIONS: Array<{ label: string; value: Platform }> = [
  { label: '\u5fae\u4fe1\u7fa4', value: '\u5fae\u4fe1' },
  { label: 'QQ\u7fa4', value: 'QQ' },
  { label: '\u9489\u9489\u7fa4', value: '\u9489\u9489' },
  { label: '\u98de\u4e66\u7fa4', value: '\u98de\u4e66' },
  { label: 'Discord', value: 'Discord' },
]

type JoinedFilter = 'all' | 'joined' | 'unjoined'
type IgnoredFilter = 'all' | 'normal' | 'ignored'

type ViewedLibraryProps = {
  groups: ViewedGroup[]
  removingKeys: string[]
  togglingJoinedKeys: string[]
  togglingIgnoredKeys: string[]
  onRemove: (viewKey: string) => void
  onToggleJoined: (viewKey: string) => void
  onToggleIgnored: (viewKey: string) => void
  onCopyQQNumber: (qqNumber: string) => void
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium' }).format(new Date(value))
}

function formatGroupLabel(platform: Platform, groupType: GroupType): string {
  if (groupType === UNKNOWN_GROUP_TYPE) {
    return platform
  }

  return `${platform} / ${groupType}`
}

function describeEntry(group: ViewedGroup): string {
  if (group.entry.type === 'qrcode') {
    return group.entry.fallbackUrl
      ? '\u5df2\u6293\u53d6\u4e8c\u7ef4\u7801\uff0c\u53ef\u76f4\u63a5\u6253\u5f00\u5165\u53e3'
      : '\u5df2\u6293\u53d6\u4e8c\u7ef4\u7801\uff0c\u53ef\u626b\u7801\u52a0\u5165'
  }

  if (group.entry.type === 'qq_number') {
    return `QQ\u7fa4\u53f7\uff1a${group.entry.qqNumber}`
  }

  return group.entry.note
}

export function ViewedLibrary({
  groups,
  removingKeys,
  togglingJoinedKeys,
  togglingIgnoredKeys,
  onRemove,
  onToggleJoined,
  onToggleIgnored,
  onCopyQQNumber,
}: ViewedLibraryProps) {
  const [search, setSearch] = useState('')
  const [selectedPlatforms, setSelectedPlatforms] = useState<Platform[]>([])
  const [joinedFilter, setJoinedFilter] = useState<JoinedFilter>('all')
  const [ignoredFilter, setIgnoredFilter] = useState<IgnoredFilter>('normal')
  const [page, setPage] = useState(1)

  const filteredGroups = useMemo(() => {
    const trimmedSearch = search.trim().toLowerCase()
    const selectedPlatformSet = new Set(selectedPlatforms)

    const filtered = groups
      .filter((group) => {
        if (trimmedSearch && !group.appName.toLowerCase().includes(trimmedSearch)) {
          return false
        }

        if (selectedPlatformSet.size > 0 && !selectedPlatformSet.has(group.platform)) {
          return false
        }

        if (joinedFilter === 'joined' && !group.isJoined) {
          return false
        }

        if (joinedFilter === 'unjoined' && group.isJoined) {
          return false
        }

        if (ignoredFilter === 'normal' && group.isIgnored) {
          return false
        }

        if (ignoredFilter === 'ignored' && !group.isIgnored) {
          return false
        }

        return true
      })
      .sort((left, right) => {
        const timeDiff = +new Date(right.viewedAt) - +new Date(left.viewedAt)
        if (timeDiff !== 0) {
          return timeDiff
        }

        return left.appName.localeCompare(right.appName, 'zh-CN')
      })

    return filtered
  }, [groups, ignoredFilter, joinedFilter, search, selectedPlatforms])

  const totalPages = Math.max(1, Math.ceil(filteredGroups.length / PAGE_SIZE))
  const safePage = Math.min(page, totalPages)
  const visibleGroups = filteredGroups.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE)

  function togglePlatform(platform: Platform) {
    setPage(1)
    setSelectedPlatforms((previous) =>
      previous.includes(platform) ? previous.filter((item) => item !== platform) : [...previous, platform],
    )
  }

  function handleSearchChange(value: string) {
    setPage(1)
    setSearch(value)
  }

  function handleJoinedFilterChange(value: JoinedFilter) {
    setPage(1)
    setJoinedFilter(value)
  }

  function handleIgnoredFilterChange(value: IgnoredFilter) {
    setPage(1)
    setIgnoredFilter(value)
  }

  if (groups.length === 0) {
    return <p className="status-note">{'\u6682\u65e0\u5165\u5e93\u7fa4\u804a\u3002'}</p>
  }

  return (
    <>
      <div className="lib-toolbar">
        <input
          aria-label={'\u6309\u5e94\u7528\u540d\u641c\u7d22'}
          className="lib-search-input"
          placeholder={'\u641c\u7d22\u5e94\u7528\u540d'}
          type="search"
          value={search}
          onChange={(event) => handleSearchChange(event.target.value)}
        />

        <div aria-label={'\u5e73\u53f0\u7b5b\u9009'} className="lib-chip-row" role="group">
          {PLATFORM_OPTIONS.map((option) => (
            <button
              key={option.value}
              aria-pressed={selectedPlatforms.includes(option.value)}
              className={`platform-filter-chip${selectedPlatforms.includes(option.value) ? ' active' : ''}`}
              type="button"
              onClick={() => togglePlatform(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>

        <div aria-label={'\u5165\u7fa4\u72b6\u6001\u7b5b\u9009'} className="lib-status-chips" role="group">
          {([
            ['all', '\u5168\u90e8'],
            ['joined', '\u5df2\u5165\u7fa4'],
            ['unjoined', '\u672a\u5165\u7fa4'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              aria-pressed={joinedFilter === value}
              className={`platform-filter-chip${joinedFilter === value ? ' active' : ''}`}
              type="button"
              onClick={() => handleJoinedFilterChange(value)}
            >
              {label}
            </button>
          ))}
        </div>

        <div aria-label={'\u5ffd\u7565\u72b6\u6001\u7b5b\u9009'} className="lib-status-chips" role="group">
          {([
            ['normal', '\u6b63\u5e38'],
            ['ignored', '\u5df2\u5ffd\u7565'],
            ['all', '\u5168\u90e8'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              aria-pressed={ignoredFilter === value}
              className={`platform-filter-chip${ignoredFilter === value ? ' active' : ''}`}
              type="button"
              onClick={() => handleIgnoredFilterChange(value)}
            >
              {label}
            </button>
          ))}
        </div>

        <span className="status-note">{`\u5171 ${filteredGroups.length} \u6761`}</span>
      </div>

      {filteredGroups.length === 0 ? (
        <p className="status-note">{'\u6ca1\u6709\u5339\u914d\u7684\u5165\u5e93\u7fa4\u804a\u3002'}</p>
      ) : (
        <>
          <div className="viewed-grid">
            {visibleGroups.map((group) => {
              const isRemoving = removingKeys.includes(group.viewKey)
              const isTogglingJoined = togglingJoinedKeys.includes(group.viewKey)
              const isTogglingIgnored = togglingIgnoredKeys.includes(group.viewKey)
              let entryAction: React.ReactNode = null

              if (group.entry.type === 'qrcode') {
                entryAction = group.entry.fallbackUrl ? (
                  <a
                    className="source-link compact-link"
                    href={group.entry.fallbackUrl}
                    rel="noreferrer"
                    target="_blank"
                  >
                    {'\u6253\u5f00\u5165\u53e3'}
                  </a>
                ) : null
              } else if (group.entry.type === 'qq_number') {
                const { qqNumber } = group.entry
                entryAction = (
                  <button
                    className="source-link compact-link"
                    type="button"
                    onClick={() => onCopyQQNumber(qqNumber)}
                  >
                    {'\u590d\u5236\u7fa4\u53f7'}
                  </button>
                )
              } else {
                entryAction = (
                  <a
                    className="source-link compact-link"
                    href={group.entry.url}
                    rel="noreferrer"
                    target="_blank"
                  >
                    {'\u6253\u5f00\u5165\u53e3'}
                  </a>
                )
              }

              return (
                <article className="viewed-card" key={group.viewKey}>
                  <div className="viewed-group-head">
                    <p className="viewed-app">{group.appName}</p>
                    <div className="group-tags">
                      <span className="tag">{group.platform}</span>
                      {group.groupType !== UNKNOWN_GROUP_TYPE ? (
                        <span className="tag muted">{group.groupType}</span>
                      ) : null}
                    </div>
                  </div>

                  <div className={group.entry.type === 'qrcode' ? 'group-entry' : 'group-entry link-only'}>
                    {group.entry.type === 'qrcode' ? (
                      <img
                        alt={`${group.appName} ${group.platform} \u4e8c\u7ef4\u7801`}
                        className="qrcode-image secondary-qrcode"
                        decoding="async"
                        loading="lazy"
                        src={group.entry.imagePath}
                      />
                    ) : null}

                    <div className="empty-state">
                      <div className="group-tags">
                        <span className="tag muted">{formatGroupLabel(group.platform, group.groupType)}</span>
                        {group.isJoined ? <span className="tag">{'\u5df2\u5165\u7fa4'}</span> : null}
                      </div>
                      <p className="status-note">{describeEntry(group)}</p>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                        {entryAction}

                        <button
                          className="viewed-toggle"
                          disabled={isTogglingJoined}
                          type="button"
                          onClick={() => onToggleJoined(group.viewKey)}
                        >
                          {isTogglingJoined
                            ? '\u5904\u7406\u4e2d...'
                            : group.isJoined
                              ? '\u5df2\u5165\u7fa4'
                              : '\u6807\u8bb0\u5165\u7fa4'}
                        </button>

                        <button
                          className="source-link viewed-remove compact-link"
                          disabled={isRemoving}
                          type="button"
                          onClick={() => onRemove(group.viewKey)}
                        >
                          {isRemoving ? '\u79fb\u9664\u4e2d...' : '\u79fb\u9664'}
                        </button>

                        <button
                          className="source-link compact-link"
                          disabled={isTogglingIgnored}
                          type="button"
                          onClick={() => onToggleIgnored(group.viewKey)}
                        >
                          {isTogglingIgnored ? '\u5904\u7406\u4e2d...' : group.isIgnored ? '\u53d6\u6d88\u5ffd\u7565' : '\u5ffd\u7565'}
                        </button>
                      </div>
                    </div>
                  </div>

                  <div className="viewed-group-head">
                    <span className="status-note">{`\u67e5\u770b\u4e8e ${formatDate(group.viewedAt)}`}</span>
                  </div>
                </article>
              )
            })}
          </div>

          {totalPages > 1 ? (
            <div className="lib-pagination">
              <button
                aria-label={'\u4e0a\u4e00\u9875'}
                className="lib-pagination-btn"
                disabled={safePage === 1}
                type="button"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                {'\u4e0a\u4e00\u9875'}
              </button>
              <span className="status-note">{`\u7b2c ${safePage} / ${totalPages} \u9875`}</span>
              <button
                aria-label={'\u4e0b\u4e00\u9875'}
                className="lib-pagination-btn"
                disabled={safePage === totalPages}
                type="button"
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              >
                {'\u4e0b\u4e00\u9875'}
              </button>
            </div>
          ) : null}
        </>
      )}
    </>
  )
}
