import { useEffect, useState } from 'react'

import { fetchHealth, type HealthPayload } from './api/health'
import { searchOfficialGroups } from './api/search'
import type { ProductCard } from './domain/types'
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
    return '—'
  }

  return new Intl.NumberFormat('zh-CN').format(value)
}

function App() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ProductCard[]>([])
  const [loading, setLoading] = useState(false)
  const [emptyMessage, setEmptyMessage] = useState<string | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [health, setHealth] = useState<HealthPayload | null>(null)

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

  async function handleSearch() {
    const trimmed = query.trim()

    if (!trimmed) {
      setSearchError('请输入产品名、常见别名或官网域名。')
      setEmptyMessage(null)
      setResults([])
      return
    }

    setLoading(true)
    setSearchError(null)

    try {
      const response = await searchOfficialGroups(trimmed)
      setResults(response.results)
      setEmptyMessage(response.emptyMessage)
    } catch (error) {
      setResults([])
      setEmptyMessage(null)
      setSearchError(
        error instanceof Error
          ? '搜索请求失败，请确认本地后端已启动后重试。'
          : '搜索请求失败，请稍后重试。',
      )
    } finally {
      setLoading(false)
    }
  }

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void handleSearch()
  }

  return (
    <div className="shell">
      <header className="hero">
        <div>
          <p className="eyebrow">MVP 最小可用闭环</p>
          <h1>AI群聊发现器</h1>
          <p className="hero-text">
            输入产品名、常见别名或官网域名，系统会实时抓取官网与 GitHub
            官方页面，返回官方群信息；搜不到时明确显示“未发现该产品的官方群”。
          </p>
        </div>
        <div className="hero-meta">
          <span className={`status-badge ${health ? 'online' : 'offline'}`}>
            {health ? '后端已连接' : '后端未连接'}
          </span>
          <p>
            本轮只实现搜索闭环，不引入登录、人工补录、团队共享状态和非 MVP
            平台。
          </p>
        </div>
      </header>

      <main className="workspace">
        <section className="panel search-panel">
          <form className="search-form" onSubmit={handleSubmit}>
            <label className="search-label" htmlFor="search-query">
              搜索输入
            </label>
            <div className="search-row">
              <input
                id="search-query"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索 AI 产品名称，如 Cursor、Claude、Lovable 或官网域名"
              />
              <button disabled={loading} type="submit">
                {loading ? '搜索中...' : '搜索'}
              </button>
            </div>
          </form>

          <div className="helper-row">
            <span>支持：产品名 / 常见别名 / 官网域名</span>
            <span>范围：官网首页、相关入口页、GitHub 官方页</span>
          </div>

          {searchError ? <p className="feedback error">{searchError}</p> : null}
        </section>

        <section className="results-section">
          {loading ? (
            <div className="skeleton-grid" aria-label="搜索加载中">
              {Array.from({ length: 2 }).map((_, index) => (
                <article className="skeleton-card" key={index}>
                  <div className="skeleton-line title" />
                  <div className="skeleton-line" />
                  <div className="skeleton-line short" />
                  <div className="skeleton-block" />
                </article>
              ))}
            </div>
          ) : null}

          {!loading && emptyMessage ? (
            <section className="panel empty-state">
              <h2>{emptyMessage}</h2>
              <p>未在官网或 GitHub 官方页面找到明确的官方群入口。</p>
              <p>你可以尝试更换产品名、常见别名，或直接输入官网域名。</p>
            </section>
          ) : null}

          {!loading && !emptyMessage && results.length > 0 ? (
            <div className="results-grid">
              {results.map((card) => (
                <article className="panel result-card" key={card.productId}>
                  <div className="card-header">
                    <div>
                      <p className="card-title">{card.appName}</p>
                      <p className="card-description">
                        {card.description || '—'}
                      </p>
                    </div>
                  </div>

                  <dl className="meta-list">
                    <div>
                      <dt>GitHub stars</dt>
                      <dd>{formatStars(card.githubStars)}</dd>
                    </div>
                    <div>
                      <dt>创建时间</dt>
                      <dd>{formatDate(card.createdAt)}</dd>
                    </div>
                    <div>
                      <dt>最近验证时间</dt>
                      <dd>{formatDate(card.verifiedAt)}</dd>
                    </div>
                  </dl>

                  <div className="groups-list">
                    {card.groups.map((group) => (
                      <section className="group-row" key={group.groupId}>
                        <div className="group-copy">
                          <div className="group-tags">
                            <span className="tag">{group.platform}</span>
                            <span className="tag muted">{group.groupType}</span>
                          </div>

                          {group.entry.type === 'qrcode' ? (
                            <div className="group-entry">
                              <img
                                alt={`${card.appName} ${group.platform}二维码`}
                                className="qrcode-image"
                                src={group.entry.imagePath}
                              />
                              <div className="group-actions">
                                <span>已抓取官方群二维码</span>
                                {group.entry.fallbackUrl ? (
                                  <a
                                    className="link-button"
                                    href={group.entry.fallbackUrl}
                                    rel="noreferrer"
                                    target="_blank"
                                  >
                                    打开官方群入口
                                  </a>
                                ) : null}
                              </div>
                            </div>
                          ) : (
                            <div className="group-entry link-only">
                              <div className="group-actions">
                                <span>{group.entry.note}</span>
                                <a
                                  className="link-button"
                                  href={group.entry.url}
                                  rel="noreferrer"
                                  target="_blank"
                                >
                                  打开官方群入口
                                </a>
                              </div>
                            </div>
                          )}
                        </div>
                      </section>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          ) : null}

          {!loading && !emptyMessage && !searchError && results.length === 0 ? (
            <section className="panel guide-state">
              <h2>开始搜索</h2>
              <p>
                输入产品名、别名或官网域名后，顶部搜索框会触发一次实时抓取。
              </p>
            </section>
          ) : null}
        </section>
      </main>
    </div>
  )
}

export default App
