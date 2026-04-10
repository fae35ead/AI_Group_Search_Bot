# AI 群聊发现器 — 产品流程图

> 本文档描述产品从"用户输入关键词"到"看到结果"的完整数据流。

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                          用户（浏览器）                                │
│                                                                       │
│   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐          │
│   │  推荐词入口   │   │  搜索框      │   │  结果列表    │          │
│   │  chips      │──▶│  关键词输入   │──▶│  产品卡片    │          │
│   └──────┬───────┘   └──────┬───────┘   └──────▲───────┘          │
│          │ GET              │ POST               │                   │
│          │ /api/recommend  │ /api/search        │                   │
└──────────│──────────────────│────────────────────│───────────────────┘
           ▼                  ▼                    │
┌─────────────────────────────────────────────────────┴─────────────────┐
│                          后端服务 (FastAPI)                             │
│                                                                        │
│   ┌──────────────────────────────────┐  ┌─────────────────────────────┐ │
│   │  RecommendationsService         │  │  SearchService.search()     │ │
│   │  GET /api/recommendations        │  │  POST /api/search           │ │
│   │  5-min TTL, top 12 repos        │  └─────────────────────────────┘ │
│   └──────────────────────────────────┘                │                 │
│                                                         ▼                 │
│         ┌──────────────────────────────────────────────────────────┐   │
│         │              搜索链路（search_service.search）              │   │
│         │                                                          │   │
│         │  ① 缓存命中 ──────────────────────────────────────────▶│   │
│         │                                                          │   │
│         │  ② GitHub Search API 3 变体并行                           │   │
│         │                                                          │   │
│         │  ③ 候选扩展（Owner / Topic / AI Topic 兜底）            │   │
│         │                                                          │   │
│         │  ④ Web 兜底（Bing + DuckDuckGo）                        │   │
│         │                                                          │   │
│         │  ⑤ 页面抓取（httpx 静态 + Playwright 浏览器视觉扫描）     │   │
│         │                                                          │   │
│         │  ⑥ 群入口提取（三层判定）                               │   │
│         │                                                          │   │
│         │  ⑦ 入库过滤（viewed_groups 表，排除已入库/已忽略）      │   │
│         │                                                          │   │
│         │  ⑧ 缓存结果（SQLite，24h TTL）                          │   │
│         └──────────────────────────────────────────────────────────┘   │
│                                                                        │
│   ┌────────────────────────────────────────────────────────────────┐  │
│   │                    SQLite 本地存储                              │  │
│   │  viewed_groups  │  search_cache  │  manual_uploads  │  ...    │  │
│   └────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────
```

---

## 二、搜索完整流程

### 用户入口

```
入口A（推荐探索）
  首页加载 → GET /api/recommendations → 展示 chips
  点击推荐词 → 复用主流搜索链路

入口B（主动搜索）
  搜索框输入 → POST /api/search
    支持: 关键词 | GitHub URL | 官网域名
```

### Step 1：缓存检查

```
_build_search_cache_key(query + filters)
  ↓
_load_cached_search(cache_key)
  ↓
TTL 内且命中 ≥ limit 条？
  ├─ 是 → 直接返回缓存结果（经 _filter_viewed_cards 过滤）
  └─ 否 → 继续 Step 2
```

### Step 2：GitHub 候选召回

```
_github_search(query, filters)
  │
  ├─ in:name     (变体A)  ───▶ 最多 40 条
  ├─ in:description (变体B) ──▶ 最多 28 条
  └─ in:readme   (变体C)  ───▶ 最多 28 条

  通用查询（AI/agent/chat 等）
    → 追加群意图变体: community / discord / 官方群 / 社群 / ...

  ThreadPoolExecutor(max_workers=4) 并行执行变体
```

### Step 3：候选扩展

```
_build_crawl_candidates(primary_github_candidates)
  │
  ├─ Owner 扩展 (user:{owner})
  │    最多 2 个
  │
  ├─ Topic 扩展 (topic:{topic})
  │    最多 3 个
  │
  └─ AI Topic 兜底 (topic:artificial-intelligence stars:>5000)
       最多 6 个

  合并去重 → deep_candidate_limit 条
```

### Step 4：Web 兜底

```
_build_web_fallback_candidates(query)
  │
  ├─ Bing RSS 搜索 (query + official site / 官网 / community)
  └─ DuckDuckGo HTML 兜底

  域名打分排序 → 最多 8 个候选
  与 GitHub 候选合并去重
```

### Step 5：页面抓取

```
_collect_candidates() — ThreadPoolExecutor(max_workers=4) 批量处理

  每批 4 个候选仓库并行执行：

  队列: [repo_url, homepage, 官网根域] + [相关内链 × 4]
          community / support / join / discord / qq / ...

  静态抓取: httpx.Client → BeautifulSoup 解析

  浏览器兜底（静态未命中 + 有 budget）:
    Playwright Chromium → 视觉扫描 canvas + background-image
    最多 3 次（官方站点 browser fallback 预算）
```

### Step 6：群入口提取（三层判定）

```
EntryExtractor.extract(pages)
  │
  ┌─ 图片扫描 ─────────────────────────────────────────────┐
  │                                                       │
  │  预过滤: 图片 URL + 上下文含平台关键词 OR 群聊意图    │
  │                                                       │
  │  ┌────────────────────────────────────────────────┐  │
  │  │  层1: OpenCV QR 解码成功 + 内容含平台关键词     │  │
  │  │  → ✅ QRCodeEntry (qrcode_verified=True)        │  │
  │  ├────────────────────────────────────────────────┤  │
  │  │  层2: 检测到 QR 特征但解码失败 + 文本含平台     │  │
  │  │  → ✅ LinkEntry (降级)                          │  │
  │  ├────────────────────────────────────────────────┤  │
  │  │  层3: 无 QR 特征 + 尺寸≥400px + 文件≥20KB     │  │
  │  │        + 含平台意图                              │  │
  │  │  → ✅ LinkEntry (降级)                          │  │
  │  └────────────────────────────────────────────────┘  │
  └────────────────────────────────────────────────────────┘

  ┌─ 链接扫描 ─────────────────────────────────────────────┐
  │                                                       │
  │  <a href> 含平台关键词 + 群意图                       │
  │  → ✅ LinkEntry                                       │
  └────────────────────────────────────────────────────────┘

  平台识别顺序: Discord > 飞书 > QQ > 微信
  企业微信 → 已合并入微信（展示层统一为"微信"）
```

### Step 7：入库过滤

```
_filter_viewed_cards(cards)
  │
  ├─ 从 viewed_groups 表加载所有已入库 record
  │
  ├─ 构建排除集合:
  │    view_key（SHA1 签名）
  │    match_key: {platform}:{qrcode_sha1}
  │               或 {platform}:link:{normalized_url}
  │               或 {platform}:qq:{qq_number}
  │
  └─ 过滤: 群已在 viewed_groups 中 → 不出现在搜索结果
```

### Step 8：缓存结果

```
_save_cached_search(cache_key, final_cards)
  → SQLite search_cache 表，24h TTL
```

---

## 三、入库管理流程

```
POST /api/groups/viewed
  { product_id, app_name, group, is_ignored }

  → viewed_groups 表 upsert
  → is_ignored=1 时该群从默认列表隐藏
  → 同步更新 viewed_qrcodes/ 和 viewed_links.csv

PATCH /api/groups/viewed/{view_key}/ignored
  → is_ignored = 1 - is_ignored（切换）

DELETE /api/groups/viewed/{view_key}
  → 从 viewed_groups 删除
  → 该群下次搜索可重新出现
```

### 入库列表筛选

```
GET /api/groups/viewed
  返回所有 ViewedGroup（含 is_joined、is_ignored）

  前端本地筛选:
  ├─ 平台筛选: 微信 | QQ | 飞书 | Discord
  ├─ 已入群筛选: 全部 | 已入群 | 未入群
  └─ 忽略筛选: 正常（默认）| 已忽略 | 全部
```

---

## 四、数据模型

```
ProductCard
  productId:          SHA1(repo_url)[:12]
  appName:           repo_name
  description:       GitHub description
  githubStars:        stargazers_count
  createdAt:          GitHub created_at
  verifiedAt:         本次搜索 UTC 时间戳
  groups:             OfficialGroup[]
  groupDiscoveryStatus: "found" | "not_found"
  officialSiteUrl:    homepage 或官网根域
  githubRepoUrl:     repo URL

OfficialGroup
  groupId:            SHA1(platform + entry_sig)[:16]
  platform:           微信 | QQ | 飞书 | Discord | 钉钉
  groupType:          交流群 | 答疑群 | 售后群 | 招募/内测群 | 未知
  entry:              QRCodeEntry | LinkEntry | QQNumberEntry
  isAdded:            false（搜索结果不使用）
  sourceUrls:         [发现该入口的页面 URL]

ViewedGroup（SQLite viewed_groups 表）
  viewKey:            主键（群唯一标识）
  productId:          所属产品 ID
  appName:            产品名
  platform:           平台
  groupType:          群类型
  entry_type:         qrcode | link | qq_number
  entry_url:          链接或 QQ 群号
  image_path:         二维码图片路径
  fallback_url:        备用链接
  viewed_at:          入库时间
  is_joined:          是否已入群（0/1）
  is_ignored:         是否忽略（0/1）
```

---

## 五、API 响应示例

### POST /api/search

```json
{
  "query": "FastGPT",
  "results": [
    {
      "product_id": "abc123def456",
      "app_name": "FastGPT",
      "description": "...",
      "github_stars": 25000,
      "created_at": "2023-07-01T00:00:00Z",
      "verified_at": "2026-04-09T12:00:00Z",
      "groups": [
        {
          "group_id": "xyz789",
          "platform": "飞书",
          "group_type": "交流群",
          "entry": {
            "type": "qrcode",
            "image_path": "/assets/qrcodes/fastgpt_飞书_a1b2c3d4.png",
            "fallback_url": "https://..."
          },
          "is_added": false,
          "source_urls": ["https://github.com/labring/fastgpt"]
        }
      ],
      "group_discovery_status": "found",
      "official_site_url": "https://fastgpt.plus",
      "github_repo_url": "https://github.com/labring/fastgpt"
    }
  ],
  "empty_message": null
}
```

### GET /api/groups/viewed

```json
{
  "groups": [
    {
      "view_key": "xyz789",
      "product_id": "abc123def456",
      "app_name": "FastGPT",
      "platform": "飞书",
      "group_type": "交流群",
      "entry": { "type": "qrcode", "image_path": "...", "fallback_url": "..." },
      "viewed_at": "2026-04-09T12:00:00Z",
      "is_joined": false,
      "is_ignored": false
    }
  ]
}
```
