import { useEffect, useMemo, useRef, useState } from 'react'

import { fetchHealth, type HealthPayload } from './api/health'
import { fetchRecommendations, fetchViewedGroups, manualUploadGroup, markGroupViewed, removeViewedGroup, searchOfficialGroups } from './api/search'
import { ResultCard } from './components/ResultCard'
import { isUnknownGroupType } from './domain/groupDisplay'
import { canonicalizePlatform } from './domain/platform'
import type { GroupType, ManualEntryType, OfficialGroup, Platform, ProductCard, RecommendedTool, SearchFilters, ViewedGroup } from './domain/types'
import './App.css'

function formatDate(value: string | null) {
  if (!value) {
    return '未知'
  }

  return new Intl.DateTimeFormat('zh-CN', {
    dateStyle: 'medium',
    timeStyle: value.includes('T') ? 'short' : undefined,
  }).format(new Date(value))
}

function formatStars(value: number | null) {
  if (value === null) {
    return '-'
  }

  return new Intl.NumberFormat('zh-CN').format(value)
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

const VIEWED_GROUPS_STORAGE_KEY = 'ai-group-discovery:viewed-groups'

function mergeViewedGroups(localGroups: ViewedGroup[], remoteGroups: ViewedGroup[]): ViewedGroup[] {
  const merged = new Map<string, ViewedGroup>()
  for (const group of localGroups) {
    if (group?.viewKey) {
      merged.set(group.viewKey, group)
    }
  }
  for (const group of remoteGroups) {
    if (group?.viewKey && !merged.has(group.viewKey)) {
      merged.set(group.viewKey, group)
    }
  }
  return Array.from(merged.values()).sort((a, b) => +new Date(b.viewedAt) - +new Date(a.viewedAt))
}

function readViewedGroupsFromStorage(): ViewedGroup[] {
  if (typeof window === 'undefined') {
    return []
  }
  try {
    const raw = window.localStorage.getItem(VIEWED_GROUPS_STORAGE_KEY)
    if (!raw) {
      return []
    }
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return []
    }
    return parsed
      .filter((item): item is ViewedGroup => typeof item?.viewKey === 'string')
      .map((item) => ({
        ...item,
        platform: canonicalizePlatform(item.platform as Platform | '企业微信'),
      }))
  } catch {
    return []
  }
}

function persistViewedGroupsToStorage(groups: ViewedGroup[]) {
  if (typeof window === 'undefined') {
    return
  }
  try {
    window.localStorage.setItem(VIEWED_GROUPS_STORAGE_KEY, JSON.stringify(groups))
  } catch {
    // Ignore storage write failures.
  }
}

type ManualUploadFormState = {
  appName: string
  description: string
  createdAt: string
  githubStars: string
  platform: Platform
  groupType: GroupType
  entryType: ManualEntryType
  entryUrl: string
  fallbackUrl: string
  qrcodeFile: File | null
}

type SearchMode = 'initial' | 'expand' | 'verify'

const PLATFORM_OPTIONS: Array<{ label: string; value: Platform }> = [
  { label: '微信', value: '\u5fae\u4fe1' },
  { label: 'QQ', value: 'QQ' },
  { label: '飞书', value: '\u98de\u4e66' },
  { label: 'Discord', value: 'Discord' },
  { label: '企业微信', value: '\u4f01\u4e1a\u5fae\u4fe1' },
  { label: '钉钉', value: '\u9489\u9489' },
].filter((option) => option.value !== '\u4f01\u4e1a\u5fae\u4fe1') as Array<{ label: string; value: Platform }>

const GROUP_PLATFORM_FILTER_OPTIONS: Array<{ label: string; value: Platform }> = [
  { label: '微信群', value: '\u5fae\u4fe1' },
  { label: 'QQ群', value: 'QQ' },
  { label: '钉钉群', value: '\u9489\u9489' },
  { label: '飞书群', value: '\u98de\u4e66' },
  { label: 'Discord', value: 'Discord' },
]

const GROUP_TYPE_OPTIONS: Array<{ label: string; value: GroupType }> = [
  { label: '未知', value: '\u672a\u77e5' },
  { label: '交流群', value: '\u4ea4\u6d41\u7fa4' },
  { label: '答疑群', value: '\u7b54\u7591\u7fa4' },
  { label: '售后群', value: '\u552e\u540e\u7fa4' },
  { label: '招募/内测群', value: '\u62db\u52df/\u5185\u6d4b\u7fa4' },
]

const INITIAL_MANUAL_FORM: ManualUploadFormState = {
  appName: '',
  description: '',
  createdAt: '',
  githubStars: '',
  platform: '\u5fae\u4fe1',
  groupType: '\u672a\u77e5',
  entryType: 'qrcode',
  entryUrl: '',
  fallbackUrl: '',
  qrcodeFile: null,
}

function App() {
  const [query, setQuery] = useState('')
  const [rawResults, setRawResults] = useState<ProductCard[]>([])
  const [searchMode, setSearchMode] = useState<SearchMode | null>(null)
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [health, setHealth] = useState<HealthPayload | null>(null)
  const [minStars, setMinStars] = useState('')
  const [createdAfter, setCreatedAfter] = useState('')
  const [createdBefore, setCreatedBefore] = useState('')
  const [selectedGroupPlatforms, setSelectedGroupPlatforms] = useState<Platform[]>([])
  const [resultLimit, setResultLimit] = useState(10)
  const [recommendations, setRecommendations] = useState<RecommendedTool[]>([])
  const [refreshingRecommendations, setRefreshingRecommendations] = useState(false)
  const [viewedGroups, setViewedGroups] = useState<ViewedGroup[]>(() => readViewedGroupsFromStorage())
  const [viewedExpanded, setViewedExpanded] = useState(false)
  const [markingGroupIds, setMarkingGroupIds] = useState<string[]>([])
  const [removingViewedKeys, setRemovingViewedKeys] = useState<string[]>([])
  const [expandedViewedApps, setExpandedViewedApps] = useState<string[]>([])
  const [expandedResultCards, setExpandedResultCards] = useState<string[]>([])
  const [loadingViewed, setLoadingViewed] = useState(false)
  const [manualUploadOpen, setManualUploadOpen] = useState(false)
  const [manualUploading, setManualUploading] = useState(false)
  const [manualUploadError, setManualUploadError] = useState<string | null>(null)
  const [manualForm, setManualForm] = useState<ManualUploadFormState>(INITIAL_MANUAL_FORM)
  const [submittedQuery, setSubmittedQuery] = useState('')
  const activeSearchControllerRef = useRef<AbortController | null>(null)
  const activeSearchRequestRef = useRef<{ query: string; limit: number; mode: SearchMode } | null>(null)
  const lastCompletedSearchRef = useRef<{ query: string; limit: number } | null>(null)

  const loading = searchMode === 'initial' || searchMode === 'expand'
  const verifying = searchMode === 'verify'
  const showInitialSkeleton = searchMode === 'initial'

  const displayedResults = useMemo(() => {
    let filtered = rawResults
    if (minStars.trim()) {
      const threshold = parseInt(minStars, 10)
      if (!Number.isNaN(threshold) && threshold >= 0) {
        filtered = filtered.filter((card) => (card.githubStars ?? 0) >= threshold)
      }
    }
    if (createdAfter.trim()) {
      const after = new Date(createdAfter.trim())
      if (!Number.isNaN(after.getTime())) {
        filtered = filtered.filter((card) => {
          if (!card.createdAt) return false
          return new Date(card.createdAt) >= after
        })
      }
    }
    if (createdBefore.trim()) {
      const before = new Date(createdBefore.trim())
      if (!Number.isNaN(before.getTime())) {
        filtered = filtered.filter((card) => {
          if (!card.createdAt) return false
          return new Date(card.createdAt) <= before
        })
      }
    }
    if (selectedGroupPlatforms.length > 0) {
      const selectedPlatforms = new Set(selectedGroupPlatforms)
      filtered = filtered
        .map((card) => {
          const filteredGroups = card.groups.filter((group) => selectedPlatforms.has(group.platform))
          if (filteredGroups.length === 0) {
            return null
          }
          return filteredGroups.length === card.groups.length ? card : { ...card, groups: filteredGroups }
        })
        .filter((card): card is ProductCard => card !== null)
    }
    return filtered.slice(0, resultLimit)
  }, [rawResults, minStars, createdAfter, createdBefore, selectedGroupPlatforms, resultLimit])

  const viewedGroupsByApp = useMemo(() => {
    const buckets = new Map<string, { key: string; productId: string; appName: string; groups: ViewedGroup[] }>()
    for (const group of viewedGroups) {
      const key = `${group.productId}:${group.appName}`
      const existing = buckets.get(key)
      if (existing) {
        existing.groups.push(group)
      } else {
        buckets.set(key, {
          key,
          productId: group.productId,
          appName: group.appName,
          groups: [group],
        })
      }
    }

    const grouped = Array.from(buckets.values())
    for (const item of grouped) {
      item.groups.sort((a, b) => +new Date(b.viewedAt) - +new Date(a.viewedAt))
    }
    grouped.sort((a, b) => a.appName.localeCompare(b.appName, 'zh-CN'))
    return grouped
  }, [viewedGroups])

  const hasSearchContext = loading || rawResults.length > 0 || Boolean(emptyMessage) || Boolean(searchError)

  useEffect(() => {
    async function loadHealth() {
      try {
        const payload = await fetchHealth()
        setHealth(payload)
      } catch {
        setHealth(null)
      }
    }

    void loadHealth()
  }, [])

  async function loadRecommendations(forceRefresh = false) {
    if (forceRefresh) {
      setRefreshingRecommendations(true)
    }

    try {
      const response = await fetchRecommendations(forceRefresh)
      setRecommendations(response.tools)
    } catch {
      // Recommendations are non-critical.
    } finally {
      if (forceRefresh) {
        setRefreshingRecommendations(false)
      }
    }
  }

  async function loadViewedList() {
    setLoadingViewed(true)
    try {
      const groups = await fetchViewedGroups()
      setViewedGroups((previous) => mergeViewedGroups(previous, groups))
    } catch {
      // Keep local list when fetch fails.
    } finally {
      setLoadingViewed(false)
    }
  }

  useEffect(() => {
    void loadRecommendations()
    const timer = window.setInterval(() => {
      void loadRecommendations(true)
    }, 30 * 60 * 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    void loadViewedList()
  }, [])

  useEffect(() => {
    setExpandedViewedApps((prev) => prev.filter((key) => viewedGroupsByApp.some((item) => item.key === key)))
  }, [viewedGroupsByApp])

  useEffect(() => {
    setExpandedResultCards((prev) => prev.filter((id) => displayedResults.some((item) => item.productId === id)))
  }, [displayedResults])

  useEffect(() => {
    persistViewedGroupsToStorage(viewedGroups)
  }, [viewedGroups])

  useEffect(() => {
    const activeRequest = activeSearchRequestRef.current
    if (!activeRequest || activeRequest.mode !== 'expand') {
      return
    }
    if (resultLimit >= activeRequest.limit) {
      return
    }
    activeSearchControllerRef.current?.abort()
  }, [resultLimit])

  useEffect(() => {
    const trimmedSubmittedQuery = submittedQuery.trim()
    if (!trimmedSubmittedQuery || searchMode !== null) {
      return
    }
    if (resultLimit <= rawResults.length) {
      return
    }

    const lastCompletedSearch = lastCompletedSearchRef.current
    if (!lastCompletedSearch || lastCompletedSearch.query !== trimmedSubmittedQuery) {
      return
    }
    if (lastCompletedSearch.limit >= resultLimit) {
      return
    }

    const timer = window.setTimeout(() => {
      void runSearch(trimmedSubmittedQuery, 'expand', resultLimit)
    }, 400)

    return () => window.clearTimeout(timer)
  }, [rawResults.length, resultLimit, searchMode, submittedQuery])

  useEffect(() => {
    return () => {
      activeSearchControllerRef.current?.abort()
      activeSearchControllerRef.current = null
      activeSearchRequestRef.current = null
    }
  }, [])

  function buildFilters(): SearchFilters | undefined {
    const filters: SearchFilters = {}
    const parsedMinStars = minStars.trim() ? parseInt(minStars, 10) : Number.NaN
    if (!Number.isNaN(parsedMinStars) && parsedMinStars >= 0) {
      filters.minStars = parsedMinStars
    }
    if (createdAfter.trim()) {
      filters.createdAfter = createdAfter.trim()
    }
    if (createdBefore.trim()) {
      filters.createdBefore = createdBefore.trim()
    }
    return Object.keys(filters).length > 0 ? filters : undefined
  }

  async function runSearch(trimmedQuery: string, mode: SearchMode, requestedLimit = resultLimit) {
    activeSearchControllerRef.current?.abort()
    const controller = new AbortController()
    activeSearchControllerRef.current = controller
    activeSearchRequestRef.current = { query: trimmedQuery, limit: requestedLimit, mode }

    setSearchError(null)
    setSearchMode(mode)
    if (mode !== 'verify') {
      setSubmittedQuery(trimmedQuery)
    }
    if (mode === 'initial') {
      setQuery(trimmedQuery)
    }

    let aborted = false
    try {
      const response = await searchOfficialGroups(trimmedQuery, buildFilters(), {
        refresh: mode === 'verify',
        signal: controller.signal,
        limit: requestedLimit,
      })
      const unique = Array.from(new Map(response.results.map((card) => [card.productId, card])).values())
      setRawResults(unique)
      setEmptyMessage(response.emptyMessage)
    } catch (error) {
      if (isAbortError(error)) {
        aborted = true
        return
      }
      if (mode === 'initial') {
        setRawResults([])
        setEmptyMessage(null)
        setSearchError(
          error instanceof Error
            ? '搜索请求失败，请确认本地后端已启动后再试。'
            : '搜索请求失败，请稍后重试。',
        )
      }
    } finally {
      const isLatest = activeSearchControllerRef.current === controller
      if (isLatest) {
        activeSearchControllerRef.current = null
        activeSearchRequestRef.current = null
        setSearchMode(null)
        if (!aborted && mode !== 'verify') {
          lastCompletedSearchRef.current = { query: trimmedQuery, limit: requestedLimit }
        }
      }
    }
  }

  async function handleSearch(nextQuery?: string) {
    const trimmed = (nextQuery ?? query).trim()

    if (!trimmed) {
      setSearchError('请输入 AI 工具名、关键词、官网域名或 GitHub 仓库。')
      setEmptyMessage(null)
      setRawResults([])
      return
    }

    await runSearch(trimmed, 'initial')
  }

  async function handleVerify() {
    const trimmed = submittedQuery.trim() || query.trim()
    if (!trimmed || rawResults.length === 0) return

    await runSearch(trimmed, 'verify')
  }

  async function handleMarkViewed(card: ProductCard, group: OfficialGroup) {
    if (markingGroupIds.includes(group.groupId)) {
      return
    }

    setMarkingGroupIds((prev) => [...prev, group.groupId])
    try {
      await markGroupViewed(card, group)
      setRawResults((prev) =>
        prev
          .map((item) => {
            if (item.productId !== card.productId) {
              return item
            }
            return {
              ...item,
              groups: item.groups.filter((entry) => entry.groupId !== group.groupId),
            }
          })
          .filter((item) => item.groups.length > 0),
      )
      setViewedExpanded(true)
      const appKey = `${card.productId}:${card.appName}`
      setExpandedViewedApps((prev) => (prev.includes(appKey) ? prev : [...prev, appKey]))
      await loadViewedList()
    } catch {
      // Keep current UI on mark failure.
    } finally {
      setMarkingGroupIds((prev) => prev.filter((id) => id !== group.groupId))
    }
  }

  async function handleRemoveViewed(viewKey: string) {
    if (removingViewedKeys.includes(viewKey)) {
      return
    }

    setRemovingViewedKeys((prev) => [...prev, viewKey])
    try {
      await removeViewedGroup(viewKey)
      setViewedGroups((prev) => prev.filter((item) => item.viewKey !== viewKey))
    } catch {
      // Keep current UI on remove failure.
    } finally {
      setRemovingViewedKeys((prev) => prev.filter((id) => id !== viewKey))
    }
  }

  function resetManualForm() {
    setManualForm(INITIAL_MANUAL_FORM)
    setManualUploadError(null)
  }

  function handleManualTextChange(event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) {
    const { name, value } = event.target
    setManualForm((prev) => {
      if (name === 'platform') {
        return { ...prev, platform: value as Platform }
      }
      if (name === 'groupType') {
        return { ...prev, groupType: value as GroupType }
      }
      if (name === 'entryType') {
        return { ...prev, entryType: value as ManualEntryType }
      }
      if (name === 'appName') {
        return { ...prev, appName: value }
      }
      if (name === 'description') {
        return { ...prev, description: value }
      }
      if (name === 'createdAt') {
        return { ...prev, createdAt: value }
      }
      if (name === 'githubStars') {
        return { ...prev, githubStars: value }
      }
      if (name === 'entryUrl') {
        return { ...prev, entryUrl: value }
      }
      if (name === 'fallbackUrl') {
        return { ...prev, fallbackUrl: value }
      }
      return prev
    })
  }

  function handleManualFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null
    setManualForm((prev) => ({ ...prev, qrcodeFile: file }))
  }

  function toggleManualUpload() {
    setManualUploadOpen((prev) => {
      const next = !prev
      if (!next) {
        resetManualForm()
      }
      return next
    })
  }

  async function handleManualUploadSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setManualUploadError(null)

    const trimmedAppName = manualForm.appName.trim()
    if (!trimmedAppName) {
      setManualUploadError('请填写 AI 工具名称。')
      return
    }
    if (manualForm.entryType === 'link' && !manualForm.entryUrl.trim()) {
      setManualUploadError('链接模式下请填写群入口链接。')
      return
    }
    if (manualForm.entryType === 'qrcode' && !manualForm.qrcodeFile) {
      setManualUploadError('二维码模式下请上传二维码图片。')
      return
    }

    setManualUploading(true)
    try {
      const parsedStars = manualForm.githubStars.trim() ? parseInt(manualForm.githubStars, 10) : Number.NaN
      await manualUploadGroup({
        appName: trimmedAppName,
        description: manualForm.description.trim() || undefined,
        createdAt: manualForm.createdAt || undefined,
        githubStars: !Number.isNaN(parsedStars) && parsedStars >= 0 ? parsedStars : undefined,
        platform: manualForm.platform,
        groupType: manualForm.groupType,
        entryType: manualForm.entryType,
        entryUrl: manualForm.entryUrl.trim() || undefined,
        fallbackUrl: manualForm.fallbackUrl.trim() || undefined,
        qrcodeFile: manualForm.qrcodeFile ?? undefined,
      })
      setViewedExpanded(true)
      await loadViewedList()
      resetManualForm()
      setManualUploadOpen(false)
    } catch {
      setManualUploadError('手动上传失败，请稍后重试。')
    } finally {
      setManualUploading(false)
    }
  }

  function toggleViewedApp(appKey: string) {
    setExpandedViewedApps((prev) => (prev.includes(appKey) ? prev.filter((id) => id !== appKey) : [...prev, appKey]))
  }

  function toggleResultCard(productId: string) {
    setExpandedResultCards((prev) => (prev.includes(productId) ? prev.filter((id) => id !== productId) : [...prev, productId]))
  }

  function toggleGroupPlatform(platform: Platform) {
    setSelectedGroupPlatforms((prev) =>
      prev.includes(platform) ? prev.filter((item) => item !== platform) : [...prev, platform],
    )
  }

  async function handleCopyQQNumber(qqNumber: string) {
    try {
      await navigator.clipboard.writeText(qqNumber)
    } catch {
      // Ignore clipboard failures on unsupported environments.
    }
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void handleSearch()
  }

  function handleRecommendationClick(tool: RecommendedTool) {
    const fallback = tool.fullName.trim()
    const source = tool.name.trim() || fallback
    const segments = source.split('/').map((segment) => segment.trim()).filter(Boolean)
    const recommendationQuery = segments.length > 0 ? segments[segments.length - 1] : source
    if (!recommendationQuery) {
      return
    }
    void handleSearch(recommendationQuery)
  }

  return (
    <div className="shell">
      <header className="brand-bar section-intro" style={{ '--i': 0 } as React.CSSProperties}>
        <div className="brand-lockup">
          <p className="eyebrow">AI 群聊发现器</p>
          <h1 className="visually-hidden">AI 群聊发现器</h1>
        </div>
        <div className="brand-status">
          <span className={`status-badge ${health ? 'online' : 'offline'}`}>{health ? '后端已连接' : '后端未连接'}</span>
        </div>
      </header>

      <main className="workspace">
        <section className="panel search-panel search-panel-compact section-intro" style={{ '--i': 1 } as React.CSSProperties}>
          <form className="search-form" onSubmit={handleSubmit}>
            <label className="search-label" htmlFor="search-query">
              搜索输入
            </label>
            <div className="search-row search-row-compact">
              <input
                id="search-query"
                type="search"
                autoComplete="off"
                enterKeyHint="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="例如 ChatGPT、FastGPT、n8n、cursor.com、github.com/labring/FastGPT"
              />
              <button className="action-button primary" disabled={loading || verifying} type="submit">
                {loading ? '搜索中…' : '搜索'}
              </button>
              <button
                disabled={rawResults.length === 0 || loading || verifying}
                onClick={() => void handleVerify()}
                className="action-button secondary"
                type="button"
              >
                {verifying ? '验证中…' : '重新验证'}
              </button>
            </div>
          </form>

          <div className="filter-row filter-row-compact">
            <div className="filter-group filter-group-stars">
              <label className="filter-label" htmlFor="filter-min-stars">
                最低星级
              </label>
              <input
                id="filter-min-stars"
                className="filter-input filter-input-compact"
                type="number"
                min="0"
                placeholder="例如 50"
                value={minStars}
                onChange={(event) => setMinStars(event.target.value)}
              />
            </div>

            <div className="filter-group filter-group-dates">
              <label className="filter-label" htmlFor="filter-created-after">
                创建时间
              </label>
              <div className="filter-date-range">
                <div className="date-input-wrap">
                  <input
                    id="filter-created-after"
                    className={`filter-input filter-date-input${createdAfter ? '' : ' empty'}`}
                    type="date"
                    value={createdAfter}
                    onChange={(event) => setCreatedAfter(event.target.value)}
                  />
                  {!createdAfter ? <span className="date-input-overlay">年/月/日</span> : null}
                </div>
                <span className="filter-sep">至</span>
                <div className="date-input-wrap">
                  <input
                    id="filter-created-before"
                    className={`filter-input filter-date-input${createdBefore ? '' : ' empty'}`}
                    type="date"
                    value={createdBefore}
                    onChange={(event) => setCreatedBefore(event.target.value)}
                  />
                  {!createdBefore ? <span className="date-input-overlay">年/月/日</span> : null}
                </div>
              </div>
            </div>

            <div className="filter-group filter-group-platforms">
              <span className="filter-label">群聊平台</span>
              <div className="platform-filter-list" role="group" aria-label="群聊平台筛选">
                {GROUP_PLATFORM_FILTER_OPTIONS.map((option) => {
                  const isSelected = selectedGroupPlatforms.includes(option.value)
                  return (
                    <button
                      key={option.value}
                      aria-pressed={isSelected}
                      className={`platform-filter-chip${isSelected ? ' active' : ''}`}
                      onClick={() => toggleGroupPlatform(option.value)}
                      type="button"
                    >
                      {option.label}
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="filter-group filter-group-limit">
              <label className="filter-label" htmlFor="filter-result-limit">
                返回数量
              </label>
              <div className="result-limit-control">
                <input
                  id="filter-result-limit"
                  className="filter-range"
                  type="range"
                  min="3"
                  max="50"
                  step="1"
                  value={resultLimit}
                  onChange={(event) => setResultLimit(parseInt(event.target.value, 10))}
                />
                <span className="filter-value">{resultLimit}</span>
              </div>
            </div>
          </div>

          <div className="search-utility-row">
            <button
              aria-expanded={manualUploadOpen}
              className="manual-upload-toggle utility-trigger"
              onClick={toggleManualUpload}
              type="button"
            >
              {manualUploadOpen ? '收起手动上传' : '手动上传'}
            </button>
            <p className="search-helper-copy">
              支持：AI 工具名 / 关键词 / 官网域名 / GitHub 仓库。结果：可调返回数量（3-50），筛选即时生效。
            </p>
          </div>

          {manualUploadOpen ? (
            <form className="manual-upload-form" onSubmit={handleManualUploadSubmit}>
              <div className="manual-grid">
                <label className="manual-field">
                  <span>AI 工具名称</span>
                  <input
                    name="appName"
                    onChange={handleManualTextChange}
                    placeholder="例如 FastGPT"
                    required
                    value={manualForm.appName}
                  />
                </label>
                <label className="manual-field">
                  <span>描述（可选）</span>
                  <input
                    name="description"
                    onChange={handleManualTextChange}
                    placeholder="一句话描述"
                    value={manualForm.description}
                  />
                </label>
                <label className="manual-field">
                  <span>创建时间（可选）</span>
                  <div className="date-input-wrap">
                    <input
                      className={`filter-date-input${manualForm.createdAt ? '' : ' empty'}`}
                      name="createdAt"
                      onChange={handleManualTextChange}
                      type="date"
                      value={manualForm.createdAt}
                    />
                    {!manualForm.createdAt ? <span className="date-input-overlay">年/月/日</span> : null}
                  </div>
                  <span className="manual-hint">年/月/日</span>
                </label>
                <label className="manual-field">
                  <span>GitHub Stars（可选）</span>
                  <input
                    min="0"
                    name="githubStars"
                    onChange={handleManualTextChange}
                    placeholder="例如 1200"
                    type="number"
                    value={manualForm.githubStars}
                  />
                </label>
                <label className="manual-field">
                  <span>平台</span>
                  <select name="platform" onChange={handleManualTextChange} value={manualForm.platform}>
                    {PLATFORM_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="manual-field">
                  <span>群类型</span>
                  <select name="groupType" onChange={handleManualTextChange} value={manualForm.groupType}>
                    {GROUP_TYPE_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="manual-field">
                  <span>入口类型</span>
                  <select
                    name="entryType"
                    onChange={handleManualTextChange}
                    value={manualForm.entryType}
                  >
                    <option value="qrcode">二维码</option>
                    <option value="link">链接</option>
                  </select>
                </label>
                <label className="manual-field">
                  <span>群入口链接（链接模式必填）</span>
                  <input
                    name="entryUrl"
                    onChange={handleManualTextChange}
                    placeholder="https://..."
                    value={manualForm.entryUrl}
                  />
                </label>
                <label className="manual-field">
                  <span>备用链接（可选）</span>
                  <input
                    name="fallbackUrl"
                    onChange={handleManualTextChange}
                    placeholder="https://..."
                    value={manualForm.fallbackUrl}
                  />
                </label>
                <label className="manual-field">
                  <span>二维码图片（二维码模式必填）</span>
                  <input accept="image/*" onChange={handleManualFileChange} type="file" />
                </label>
              </div>
              {manualUploadError ? <p className="feedback error">{manualUploadError}</p> : null}
              <div className="manual-actions">
                <button disabled={manualUploading} type="submit">
                  {manualUploading ? '上传中...' : '确认上传'}
                </button>
                <button
                  className="manual-cancel"
                  disabled={manualUploading}
                  onClick={toggleManualUpload}
                  type="button"
                >
                  取消
                </button>
              </div>
            </form>
          ) : null}

          {searchError ? <p className="feedback error">{searchError}</p> : null}
        </section>

        {recommendations.length > 0 ? (
          <section className="recommendation-strip recommendation-strip-compact section-intro" style={{ '--i': 2 } as React.CSSProperties}>
            <span className="recommendation-label">今日推荐</span>
            <button
              className="recommendation-tag"
              disabled={refreshingRecommendations}
              onClick={() => void loadRecommendations(true)}
              type="button"
            >
              {refreshingRecommendations ? '更新中...' : '换一批'}
            </button>
            <div className="recommendation-tags">
              {recommendations.map((tool) => (
                <button
                  key={tool.fullName}
                  className="recommendation-tag"
                  onClick={() => handleRecommendationClick(tool)}
                  type="button"
                  title={tool.description ?? undefined}
                >
                  <span className="tag-name">{tool.name}</span>
                  <span className="tag-stars">{formatStars(tool.stars)}</span>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        <section className="panel viewed-panel utility-panel section-intro" style={{ '--i': 3 } as React.CSSProperties}>
          <div className="viewed-header">
            <p className="viewed-title">已查看列表</p>
            <button aria-expanded={viewedExpanded} className="viewed-toggle" onClick={() => setViewedExpanded((prev) => !prev)} type="button">
              {viewedExpanded ? '收起' : `展开 (${viewedGroups.length})`}
            </button>
          </div>

          {viewedExpanded ? (
            loadingViewed ? (
              <p className="status-note">加载中...</p>
            ) : viewedGroupsByApp.length > 0 ? (
              <div className="viewed-app-grid">
                {viewedGroupsByApp.map((appItem) => {
                  const expanded = expandedViewedApps.includes(appItem.key)
                  return (
                    <article className="viewed-app-card" key={appItem.key}>
                      <div className="viewed-app-header">
                        <p className="viewed-app">{appItem.appName}</p>
                        <button aria-expanded={expanded} className="viewed-toggle" onClick={() => toggleViewedApp(appItem.key)} type="button">
                          {expanded ? `收起 (${appItem.groups.length})` : `展开 (${appItem.groups.length})`}
                        </button>
                      </div>

                      {expanded ? (
                        <div className="viewed-grid">
                          {appItem.groups.map((group) => (
                            <article className="viewed-card" key={group.viewKey}>
                              <div className="viewed-group-head">
                                <div className="group-tags">
                                  <span className="tag">{group.platform}</span>
                                  {!isUnknownGroupType(group.groupType) ? (
                                    <span className="tag muted">{group.groupType}</span>
                                  ) : null}
                                </div>
                                <button
                                  className="source-link viewed-remove"
                                  disabled={removingViewedKeys.includes(group.viewKey)}
                                  onClick={() => void handleRemoveViewed(group.viewKey)}
                                  type="button"
                                >
                                  {removingViewedKeys.includes(group.viewKey) ? '移除中...' : '移除列表'}
                                </button>
                              </div>
                              {group.entry.type === 'qrcode' ? (
                                <div className="group-entry">
                                  <img alt={`${group.appName} ${group.platform} 二维码`} className="qrcode-image" src={group.entry.imagePath} />
                                  {group.entry.fallbackUrl ? (
                                    <a className="link-button" href={group.entry.fallbackUrl} rel="noreferrer" target="_blank">
                                      打开群入口
                                    </a>
                                  ) : null}
                                </div>
                              ) : group.entry.type === 'qq_number' ? (
                                <div className="group-entry link-only">
                                  <span className="status-note">QQ群号：{'qqNumber' in group.entry ? group.entry.qqNumber : ''}</span>
                                  <button
                                    className="link-button"
                                    onClick={() => void handleCopyQQNumber('qqNumber' in group.entry ? group.entry.qqNumber : '')}
                                    type="button"
                                  >
                                    复制群号
                                  </button>
                                </div>
                              ) : (
                                <div className="group-entry link-only">
                                  <a className="link-button" href={group.entry.url} rel="noreferrer" target="_blank">
                                    打开群入口
                                  </a>
                                </div>
                              )}
                            </article>
                          ))}
                        </div>
                      ) : null}
                    </article>
                  )
                })}
              </div>
            ) : (
              <p className="status-note">暂无已查看群聊。</p>
            )
          ) : null}
        </section>

        <section className="results-section section-intro" style={{ '--i': 4 } as React.CSSProperties}>
          {!showInitialSkeleton && displayedResults.length > 0 ? (
            <div className="results-overview" aria-live="polite">
              <div>
                <p className="results-eyebrow">搜索结果</p>
                <h2>{query.trim() ? `“${query.trim()}” 的群聊入口` : '搜索结果'}</h2>
              </div>
              <div className="results-meta">
                <span>{displayedResults.length} 个结果</span>
                {rawResults.length > 0 && rawResults.length !== displayedResults.length ? (
                  <span>筛选后保留 {displayedResults.length} / {rawResults.length}</span>
                ) : null}
              </div>
            </div>
          ) : null}

          {showInitialSkeleton ? (
            <div className="skeleton-grid" aria-label="搜索加载中">
              {Array.from({ length: 3 }).map((_, index) => (
                <article className="skeleton-card" key={index}>
                  <div className="skeleton-main">
                    <div className="skeleton-line title" />
                    <div className="skeleton-line" />
                    <div className="skeleton-line short" />
                    <div className="skeleton-inline">
                      <div className="skeleton-pill" />
                      <div className="skeleton-pill short" />
                    </div>
                  </div>
                  <div className="skeleton-qr" />
                </article>
              ))}
            </div>
          ) : null}

          {!loading && emptyMessage ? (
            <section className="panel empty-state">
              <h2>{emptyMessage}</h2>
              <p>这次搜索没有发现可展示的官方群入口。</p>
              <p>可以尝试更换关键词、产品名、官网域名或具体 GitHub 仓库。</p>
            </section>
          ) : null}

          {!showInitialSkeleton && !emptyMessage && displayedResults.length > 0 ? (
            <div className="results-grid search-engine-grid">
              {displayedResults.map((card, index) => (
                <ResultCard
                  card={card}
                  expanded={expandedResultCards.includes(card.productId)}
                  formatDate={formatDate}
                  formatStars={formatStars}
                  index={index}
                  key={card.productId}
                  markingGroupIds={markingGroupIds}
                  onCopyQQNumber={(qqNumber) => void handleCopyQQNumber(qqNumber)}
                  onMarkViewed={(currentCard, group) => void handleMarkViewed(currentCard, group)}
                  onToggleExpanded={toggleResultCard}
                />
              ))}
            </div>
          ) : null}

          {!loading && !emptyMessage && !searchError && rawResults.length > 0 && displayedResults.length === 0 ? (
            <section className="panel empty-state">
              <h2>筛选后无结果</h2>
              <p>当前筛选条件下没有匹配的 AI 工具，可以调整星级、时间范围或群聊平台后再试。</p>
            </section>
          ) : null}

          {!loading && !hasSearchContext ? (
            <section className="panel guide-state">
              <h2>开始搜索</h2>
              <p>输入宽泛关键词时会返回多个相关 AI 工具；输入官网域名或 GitHub 仓库时会优先返回最相关的官方群结果。</p>
            </section>
          ) : null}
        </section>
      </main>
    </div>
  )
}

export default App
