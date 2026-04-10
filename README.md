# AI 群聊发现器

输入关键词，召回相关 AI 工具的 GitHub 仓库、官网或相关页面，从中提取官方群入口（微信 / QQ / 飞书 / Discord / 钉钉），以卡片形式展示二维码和入群链接。

---

## 快速开始

### 方式一：直接运行 EXE（推荐）

下载 `app/backend/dist/ai-group-discovery.exe` 并双击运行。浏览器自动打开，等待后端就绪即可使用。

> 已有数据时，将旧的 `data/` 文件夹与新的 exe 放在同级目录，数据自动保留。

### 方式二：源码运行

**前端**

```bash
cd app/frontend
npm install
npm run dev
# 访问 http://127.0.0.1:5173
```

**后端**

```bash
cd app/backend
# 首次
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

# 启动
uvicorn main:app --port 8000 
```

---

## 功能概览

### 搜索

| 方式 | 说明 |
|------|------|
| 关键词搜索 | 输入 AI 工具名、产品名、宽泛关键词 |
| 官网域名 | 输入域名直接定位官方站点（如 `cursor.com`） |
| GitHub URL | 输入 repo URL 直接进入该仓库 |
| 推荐词 | 首页自动加载 GitHub topic:ai 热门仓库前 12 名，点击直接搜索 |

### 召回链路

- **GitHub Search API**：3 变体并行（`in:name` / `in:description` / `in:readme`）
- **通用查询扩展**：AI / agent / chat 等宽泛词自动追加群意图变体（`community`、`AI 官方群`、`社群` 等）
- **候选扩展**：GitHub 候选不足时，通过 Owner 扩展、Topic 扩展、AI Topic 兜底补充
- **Web 兜底**：Bing + DuckDuckGo 双引擎搜索，补充未收录到 GitHub 的官网
- **浏览器兜底**：静态页面未发现群入口时，自动用 Playwright 浏览器访问官网进行视觉扫描

### 入口提取

| 平台 | 入口类型 |
|------|---------|
| 微信 / 飞书 / Discord / QQ / 钉钉 | 二维码图片（已抓取） |
| QQ | QQ 群号（已提取） |
| 以上全部 | 链接（二维码暂未抓取成功） |

三层判定提取二维码：
1. OpenCV QR 解码成功 + 内容含平台关键词 → 确认
2. 检测到 QR 特征但解码失败 + 文本含平台关键词 → 降级为链接候选
3. 无 QR 特征 + 尺寸≥400×400 + 文件≥20KB + 含平台意图 → 降级通过

### 入库管理

- **入库**：将群入口加入已入库库，同时从后续搜索结果中排除
- **忽略**：同样入库并排除，但默认不在普通入库列表中展示（可通过筛选恢复）
- **标记入群**：标记已成功加入的群
- **移除**：彻底删除记录，下次搜索可再次返回

入库列表支持按平台、已入群状态、忽略状态筛选。

### 搜索结果筛选

| 筛选项 | 说明 |
|------|------|
| 最低星级 | 过滤 GitHub stars 低于设定值的仓库 |
| 创建时间 | 过滤仓库创建时间范围 |
| 群聊平台 | 只显示特定平台的群入口（微信 / QQ / 飞书 / Discord） |
| 返回数量 | 滑条调节 3~50，默认 10 |

### 搜索缓存

- 每次搜索结果自动缓存到 SQLite（24 小时 TTL）
- 强制刷新可绕过缓存重新搜索
- 已有数据自动在结果中过滤排除

### 全自动代理模式

输入关键词后点击「全自动代理模式」，系统自动从推荐词中随机抽取关键词连续搜索，找到的所有群入口一键全部入库，无需手动操作。

### 手动补录

对未自动发现群入口的工具，可通过「手动上传」表单提交工具名、平台、群类型、入口类型（二维码图片或链接）。

---

## API 列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查（包含数据库路径和 Chromium 就绪状态） |
| `GET` | `/api/recommendations` | 推荐词（GitHub topic:ai 热门仓库，5 分钟缓存） |
| `POST` | `/api/search` | 搜索接口（支持 `refresh`、`limit` 参数） |
| `GET` | `/api/groups/viewed` | 获取已入库群列表（含 `is_joined`、`is_ignored`） |
| `POST` | `/api/groups/viewed` | 标记群入口入库（`is_ignored: true` 时为忽略） |
| `POST` | `/api/groups/viewed/bulk` | 批量入库 |
| `PATCH` | `/api/groups/viewed/{view_key}/joined` | 切换"已入群"状态 |
| `PATCH` | `/api/groups/viewed/{view_key}/ignored` | 切换"忽略"状态 |
| `DELETE` | `/api/groups/viewed/{view_key}` | 从库中移除 |
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

- `limit`：返回结果上限（3~50）
- `refresh`：是否强制刷新（跳过缓存）

### `POST /api/groups/viewed`

```json
{
  "product_id": "abc123",
  "app_name": "FastGPT",
  "group": { "group_id": "...", "platform": "微信", ... },
  "is_ignored": false
}
```

设置 `is_ignored: true` 直接将群标记为忽略。

---

## 数据模型

```typescript
// 搜索结果卡片
ProductCard {
  productId:        string   // SHA1(repo_url)[:12]
  appName:         string
  description:     string
  githubStars:     number | null
  createdAt:       string | null
  verifiedAt:       string        // 本次搜索时间
  groups:          OfficialGroup[]
  groupDiscoveryStatus: "found" | "not_found"
  officialSiteUrl: string | null
  githubRepoUrl:   string | null
}

// 群入口
OfficialGroup {
  groupId:    string
  platform:   "微信" | "QQ" | "飞书" | "Discord" | "钉钉"
  groupType:  "交流群" | "答疑群" | "售后群" | "招募/内测群" | "未知"
  entry:      QRCodeEntry | LinkEntry | QQNumberEntry
  isAdded:    boolean
  sourceUrls: string[]
}

// 入库群
ViewedGroup {
  viewKey:    string
  productId: string
  appName:    string
  platform:   Platform
  groupType:  GroupType
  entry:      GroupEntry
  viewedAt:   string
  isJoined:   boolean
  isIgnored:  boolean
}
```

---

## 技术栈

**前端**
- React 19 + Vite + TypeScript
- 纯 CSS，无 UI 框架依赖
- `AbortController` 请求取消

**后端**
- FastAPI + Uvicorn
- httpx：GitHub API、页面抓取、Web 搜索
- BeautifulSoup4：HTML 解析
- Playwright（可选）：浏览器视觉扫描
- OpenCV（可选）：QR 码检测与解码
- SQLite：缓存、已入库群、搜索历史

---

## 数据库表

| 表名 | 用途 |
|------|------|
| `viewed_groups` | 已入库群入口（含 `is_joined`、`is_ignored` 状态） |
| `search_cache` | 搜索结果缓存（24 小时 TTL） |
| `search_history` | 搜索历史 |
| `recommendation_pool` | 推荐词数据池 |
| `manual_uploads` | 手动补录数据 |

---

## 环境要求

```
Node.js >= 20
Python >= 3.11
Playwright Chromium（首次运行自动安装，或手动 python -m playwright install chromium）
```

---

## 已知限制

- GitHub API 匿名访问有频率限制，高频搜索可能触发限流
- 二维码提取依赖页面可见图片，私域邀请链接（需登录/扫码）无法抓取
- GitHub 页面无图片的仓库无法提取二维码，但仓库结果仍会返回

---

## 打包为 EXE

```bash
cd app/backend
.venv\Scripts\activate
python -m PyInstaller --noconfirm --clean ai-group-discovery.spec
```

产物在 `app/backend/dist/ai-group-discovery.exe`，分发时将 `data/` 文件夹一并打包可保留已有数据。
