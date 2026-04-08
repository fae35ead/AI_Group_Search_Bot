# AI 群聊发现器

输入关键词后，系统召回相关 AI 工具，并从每个工具的 GitHub 仓库、官网或官网相关页面中寻找官方群入口（微信 / QQ / 飞书 / Discord / 企业微信 / 钉钉），最终以卡片形式展示群二维码和入群入口。

---

## 目录结构

```
AI群聊发现器/
├── app/
│   ├── frontend/          # React 19 + Vite + TypeScript（无 UI 框架）
│   └── backend/           # FastAPI + SQLite
│       └── app/
│           ├── api/       # API 路由和 Schema 定义
│           ├── core/      # 配置（Settings）
│           ├── db/        # SQLite 初始化和连接
│           └── search/    # 搜索服务、页面抓取、图片提取
├── 产品上下文/            # PRD、架构、设计文档
├── 开发日志/              # MVP 开发进度追踪
└── OpenClaw/             # 多 Agent 协作产品上下文
```

---

## 功能概览

### 搜索入口
- **关键词搜索**：输入 AI 工具名、产品名、宽泛关键词
- **官网域名**：输入域名直接定位官方站点（如 `cursor.com`）
- **GitHub 仓库 URL**：输入 repo URL 直接进入该仓库
- **推荐词入口**：首页自动加载 GitHub topic:ai 热门仓库前 12 名作为探索入口，点击复用主流搜索链路

### 召回与扩展
- GitHub Search API 3 变体并行搜索（`in:name` / `in:description` / `in:readme`）
- 通用查询（AI / agent / chat 等）自动追加群意图变体（`AI community`、`AI 官方群` 等）
- GitHub 候选不足时，通过 Owner 扩展、Topic 扩展、AI Topic 兜底扩展相关仓库
- Bing + DuckDuckGo 双引擎 Web 搜索兜底，补充未收录到 GitHub 的官网候选

### 图片提取（三层判定）
- **预过滤**：图片 URL + 上下文文本含平台关键词或群聊意图关键词，才进入下载
- **层1**：OpenCV QR 解码成功 + 解码内容含平台关键词 → 确认为群二维码
- **层2**：检测到 QR 特征但解码失败 + URL/文本含平台关键词 → 降级为链接候选
- **层3**：无 QR 特征 + URL/文本含平台关键词 + 上下文含群意图 + 尺寸≥400×400 且文件≥20KB → 降级通过

### 支持的平台与入口类型
| 平台 | 入口类型 |
|------|---------|
| 微信 / 企业微信 / 飞书 / Discord / QQ / 钉钉 | 二维码图片（已抓取） |
| QQ | QQ 群号（已提取） |
| 以上全部 | 链接（二维码暂未抓取成功） |

### 已查看群管理
- 标记"已查看"的群入口自动从结果列表移除，汇总到侧边"已查看"面板
- 支持从面板恢复（取消标记）或彻底删除
- 标记状态通过 SQLite 持久化，后端记录 `viewed_groups` 表

### 手动补录
- 对未自动发现群入口的工具，可通过"手动补录"表单提交：
  - 填写工具名、简介、平台、群类型、入口类型（二维码图片 / 链接）
  - 上传二维码图片或填写入群链接
  - 补录数据存入 SQLite，手动录入的群入口优先展示在结果列表

### 搜索缓存
- 每次搜索结果自动缓存到 SQLite（24 小时 TTL）
- 非强制刷新时命中缓存直接返回，避免重复调用 GitHub API
- 同一查询再次搜索时，已查看的群自动过滤

---

## 环境要求

```text
Node.js >= 20
Python >= 3.11
SQLite（本地文件，无需单独安装服务）
```

---

## 如何运行

### 1. 启动前端

```powershell
cd app/frontend
npm install
npm run dev
```

前端运行在 `http://127.0.0.1:5173`，开发阶段 `/api` 和 `/assets` 代理到后端。

### 2. 启动后端

```powershell
cd app/backend
.venv\Scripts\Activate.ps1
# 首次启动前
pip install -r requirements.txt
uvicorn main:app --port 8000 --reload
```

后端启动时自动初始化 SQLite 数据库，生成以下表：

```sql
added_groups        -- 已标记"已添加"的群入口（历史功能）
viewed_groups       -- 已查看的群入口（当前使用）
search_cache        -- 搜索结果缓存（24 小时 TTL）
search_history      -- 搜索历史
manual_uploads      -- 手动补录数据
recommendation_pool -- 推荐词数据池
crawl_snapshot      -- 页面抓取快照（预留）
```

---

## API 列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/recommendations` | 关键词推荐（GitHub topic:ai 热门仓库，5 分钟缓存） |
| `POST` | `/api/search` | 搜索接口（支持 `refresh`、`limit` 参数） |
| `GET` | `/api/groups/viewed` | 获取已查看群列表 |
| `POST` | `/api/groups/viewed` | 标记群入口为"已查看" |
| `DELETE` | `/api/groups/viewed/{view_key}` | 移除已查看记录 |
| `POST` | `/api/groups/manual-upload` | 手动补录群入口 |

### `POST /api/search`

```json
{
  "query": "FastGPT",
  "filters": { "min_stars": 100 },
  "refresh": false,
  "limit": 10
}
```

- `limit`：返回结果上限，范围 3~50，默认 10
- `refresh`：是否强制刷新（跳过缓存，强制重新搜索）
- 响应：`{query, results: [ProductCard...], empty_message}`

### `GET /api/recommendations`

响应：

```json
{
  "tools": [
    {
      "name": "FastGPT",
      "full_name": "labring/fastgpt",
      "stars": 25000,
      "description": "...",
      "topics": ["ai", "llm"]
    }
  ],
  "cached_at": "2026-04-03T12:00:00Z"
}
```

---

## 数据模型

```typescript
ProductCard {
  productId:        string   // SHA1(repo_url)[:12]
  appName:         string
  description:     string
  githubStars:     number | null
  createdAt:       string | null  // GitHub 创建时间
  verifiedAt:       string        // 本次搜索时间
  groups:          OfficialGroup[]
  groupDiscoveryStatus: "found" | "not_found"
  officialSiteUrl: string | null
  githubRepoUrl:   string | null
}

OfficialGroup {
  groupId:    string
  platform:   "微信" | "QQ" | "飞书" | "Discord" | "企业微信" | "钉钉"
  groupType:  "交流群" | "答疑群" | "售后群" | "招募/内测群" | "未知"
  entry:      QRCodeEntry | LinkEntry | QQNumberEntry
  isAdded:    boolean
  sourceUrls: string[]
}

// 三种入口类型
QRCodeEntry    { type: "qrcode",   imagePath: string,  fallbackUrl?: string }
LinkEntry      { type: "link",     url: string }
QQNumberEntry  { type: "qq_number", qqNumber: string }
```

---

## 技术栈

**前端**
- React 19 + Vite + TypeScript，无 UI 框架依赖
- `useMemo` 客户端筛选，无状态管理库
- `AbortController` 请求取消，防止竞态

**后端**
- FastAPI + Uvicorn
- httpx：GitHub API、页面抓取、Web 搜索
- BeautifulSoup4：HTML 解析
- OpenCV（可选）：图片尺寸元数据 + QR 解码
- SQLite（Python 标准库）：缓存、已查看群、搜索历史

---

## 已知限制

- GitHub API 匿名访问有频率限制，高频搜索可能触发限流
- 二维码提取依赖页面可见图片，私域邀请链接（需登录/扫码）无法抓取
- 群类型（交流群/答疑群等）当前全部标注为"未知"，待扩展上下文语义分析
- 企业微信、钉钉为平台枚举支持，但当前群入口发现链路主要覆盖 GitHub + 公开页面
