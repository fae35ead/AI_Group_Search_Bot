import { formatGroupLabel } from '../domain/groupDisplay'
import type { OfficialGroup, ProductCard } from '../domain/types'

function dedupeGroups(groups: OfficialGroup[]) {
  const seen = new Set<string>()
  return groups.filter((group) => {
    if (seen.has(group.groupId)) {
      return false
    }
    seen.add(group.groupId)
    return true
  })
}

function getPrimaryGroup(groups: OfficialGroup[]) {
  const uniqueGroups = dedupeGroups(groups)
  return uniqueGroups.find((group) => group.entry.type === 'qrcode') ?? uniqueGroups[0] ?? null
}

function getGroupLabel(group: OfficialGroup) {
  return formatGroupLabel(group.platform, group.groupType)
}

function isEquivalentEntry(left: OfficialGroup['entry'], right: OfficialGroup['entry']) {
  if (left.type !== right.type) {
    return false
  }

  if (left.type === 'qrcode' && right.type === 'qrcode') {
    return left.imagePath === right.imagePath && left.fallbackUrl === right.fallbackUrl
  }

  if (left.type === 'qq_number' && right.type === 'qq_number') {
    return left.qqNumber === right.qqNumber
  }

  if (left.type === 'link' && right.type === 'link') {
    return left.url === right.url
  }

  return false
}

function isEquivalentGroup(left: OfficialGroup, right: OfficialGroup) {
  return (
    left.platform === right.platform &&
    left.groupType === right.groupType &&
    isEquivalentEntry(left.entry, right.entry)
  )
}

type ResultCardProps = {
  card: ProductCard
  index: number
  expanded: boolean
  markingGroupIds: string[]
  formatDate: (value: string | null) => string
  formatStars: (value: number | null) => string
  onCopyQQNumber: (qqNumber: string) => void
  onMarkViewed: (card: ProductCard, group: OfficialGroup) => void
  onMarkIgnored: (card: ProductCard, group: OfficialGroup) => void
  onToggleExpanded: (productId: string) => void
}

export function ResultCard({
  card,
  index,
  expanded,
  markingGroupIds,
  formatDate,
  formatStars,
  onCopyQQNumber,
  onMarkViewed,
  onMarkIgnored,
  onToggleExpanded,
}: ResultCardProps) {
  const groups = dedupeGroups(card.groups)
  const primaryGroup = getPrimaryGroup(groups)

  if (!primaryGroup) {
    return null
  }

  const expandableGroups = groups.filter(
    (group) => group.groupId !== primaryGroup.groupId && !isEquivalentGroup(group, primaryGroup),
  )
  const isPrimaryMarking = markingGroupIds.includes(primaryGroup.groupId)
  const primaryGroupLabel = getGroupLabel(primaryGroup)

  function renderGroupAccessAction(group: OfficialGroup, className: string) {
    if (group.entry.type === 'qrcode') {
      if (!group.entry.fallbackUrl) {
        return null
      }

      return (
        <a className={className} href={group.entry.fallbackUrl} rel="noreferrer" target="_blank">
          打开入口
        </a>
      )
    }

    if (group.entry.type === 'qq_number') {
      return (
        <button
          className={className}
          onClick={() => void onCopyQQNumber('qqNumber' in group.entry ? group.entry.qqNumber : '')}
          type="button"
        >
          复制群号
        </button>
      )
    }

    return (
      <a className={className} href={group.entry.url} rel="noreferrer" target="_blank">
        打开入口
      </a>
    )
  }

  return (
    <article className="result-card panel section-intro" style={{ '--i': index + 2 } as React.CSSProperties}>
      <div className="card-corner-meta" aria-label="产品元信息">
        <span className="card-corner card-corner-top-left">创建 {formatDate(card.createdAt)}</span>
        <span className="card-corner card-corner-top-right">GitHub Stars {formatStars(card.githubStars)}</span>
      </div>

      <div className="result-card-grid">
        <div className="result-main">
          <div className="card-heading">
            <p className="card-title">{card.appName}</p>
            <p className="card-description">{card.description || '暂无描述'}</p>
          </div>

          {card.officialSiteUrl || card.githubRepoUrl ? (
            <div className="card-link-row" aria-label="产品来源链接">
              {card.officialSiteUrl ? (
                <a className="source-link text-link" href={card.officialSiteUrl} rel="noreferrer" target="_blank">
                  跳转官网
                </a>
              ) : null}
              {card.githubRepoUrl ? (
                <a className="source-link text-link" href={card.githubRepoUrl} rel="noreferrer" target="_blank">
                  跳转 GitHub
                </a>
              ) : null}
            </div>
          ) : null}
        </div>

        <aside className="result-entry-pane" aria-label={`${card.appName} 群入口`}>
          <div className="entry-meta">
            <span className="entry-group-label">{primaryGroupLabel}</span>
          </div>

          {primaryGroup.entry.type === 'qrcode' ? (
            <div className="entry-qr-shell">
              <img
                alt={`${card.appName} ${primaryGroup.platform} 二维码`}
                className="qrcode-image primary-qrcode"
                decoding="async"
                loading="lazy"
                src={primaryGroup.entry.imagePath}
              />
              {primaryGroup.entry.fallbackUrl ? (
                <a className="entry-link-button" href={primaryGroup.entry.fallbackUrl} rel="noreferrer" target="_blank">
                  打开群入口
                </a>
              ) : null}
            </div>
          ) : primaryGroup.entry.type === 'qq_number' ? (
            <div className="entry-link-stack qq-entry-stack">
              <span className="entry-qq-number">{'qqNumber' in primaryGroup.entry ? primaryGroup.entry.qqNumber : ''}</span>
            </div>
          ) : (
            <div className="entry-link-stack">
              <a className="entry-link-button" href={primaryGroup.entry.url} rel="noreferrer" target="_blank">
                打开群入口
              </a>
            </div>
          )}
        </aside>
      </div>

      <div className="card-utility-row">
        <div className="card-action-strip">
          {renderGroupAccessAction(primaryGroup, 'source-link compact-link inline-action access-action')}
          <button
            className="mini-action inline-action"
            disabled={isPrimaryMarking}
            onClick={() => void onMarkViewed(card, primaryGroup)}
            type="button"
          >
            {isPrimaryMarking ? '处理中…' : '入库'}
          </button>
          <button
            className="mini-action secondary-mark inline-action"
            disabled={isPrimaryMarking}
            onClick={() => void onMarkIgnored(card, primaryGroup)}
            type="button"
          >
            忽略
          </button>
        </div>

        {expandableGroups.length > 0 ? (
          <button
            aria-controls={`extra-groups-${card.productId}`}
            aria-expanded={expanded}
            className="expand-toggle"
            onClick={() => onToggleExpanded(card.productId)}
            type="button"
          >
            {expanded ? `收起其余 ${expandableGroups.length} 个入口` : `展开其余 ${expandableGroups.length} 个入口`}
          </button>
        ) : null}
      </div>

      {expandableGroups.length > 0 ? (
        <div className="group-expansion">
          <div className={`collapsible ${expanded ? 'open' : ''}`} id={`extra-groups-${card.productId}`}>
            <div className="collapsible-inner">
              <div className="secondary-group-list">
                {expandableGroups.map((group) => {
                  const isMarking = markingGroupIds.includes(group.groupId)

                  return (
                    <section className="secondary-group-row" key={group.groupId}>
                      <div className="secondary-group-main">
                        <div className="secondary-group-label">{getGroupLabel(group)}</div>

                        {group.entry.type === 'qrcode' ? (
                          <div className="secondary-group-content">
                            <div className="secondary-entry-cluster">
                              <img
                                alt={`${card.appName} ${group.platform} 二维码`}
                                className="qrcode-image secondary-qrcode"
                                decoding="async"
                                loading="lazy"
                                src={group.entry.imagePath}
                              />
                              <span className="secondary-entry-text">
                                {group.entry.fallbackUrl ? '已抓取二维码' : '扫码进入'}
                              </span>
                            </div>
                          </div>
                        ) : group.entry.type === 'qq_number' ? (
                          <div className="secondary-group-content">
                            <div className="secondary-entry-cluster">
                              <span className="secondary-entry-text secondary-qq-number">
                                {'qqNumber' in group.entry ? group.entry.qqNumber : ''}
                              </span>
                            </div>
                          </div>
                        ) : null}
                      </div>

                      <div className="secondary-group-actions">
                        {renderGroupAccessAction(group, 'source-link compact-link inline-action access-action')}
                        <button
                          className="mini-action secondary-mark inline-action"
                          disabled={isMarking}
                          onClick={() => void onMarkViewed(card, group)}
                          type="button"
                        >
                          {isMarking ? '处理中…' : '入库'}
                        </button>

                        <button
                          className="mini-action secondary-mark inline-action"
                          disabled={isMarking}
                          onClick={() => void onMarkIgnored(card, group)}
                          type="button"
                        >
                          忽略
                        </button>
                      </div>
                    </section>
                  )
                })}
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </article>
  )
}
