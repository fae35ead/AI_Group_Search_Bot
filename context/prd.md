# AI 群聊发现器 — 产品需求文档

## 1. 产品定位

AI 群聊发现器定位为：**GitHub AI 仓库搜索 + 群入口发现**工具，帮助用户快速找到目标 AI 产品在 GitHub 或官网上公开露出的官方群入口。

目标用户是调研人员和需要加入产品官方群聊的内部使用者。

---

## 2. 核心场景

1. 用户输入关键词（`FastGPT`、`RAG`、`agent` 等）
2. 系统调用 GitHub Search API 搜索相关仓库，结合 Web 搜索补充官网候选
3. 对每个候选仓库/官网抓取页面，提取群入口
4. 返回产品卡片列表，每张卡片展示仓库信息和群入口

---

## 3. 功能范围

### 3.1 搜索入口

| 入口 | 说明 |
|------|------|
| 关键词搜索 | AI 工具名、产品名、宽泛关键词 |
| 官网域名 | 输入域名直接定位（如 `cursor.com`） |
| GitHub URL | 输入 repo URL 直接进入该仓库 |
| 推荐词 | 首页加载 GitHub topic:ai 热门仓库前 12 名，点击复用搜索链路 |

### 3.2 召回链路

- **GitHub Search API**：3 变体并行（`in:name` / `in:description` / `in:readme`），通用查询自动追加群意图变体
- **候选扩展**：GitHub 候选不足时，通过 Owner 扩展、Topic 扩展、AI Topic 兜底补充
- **Web 兜底**：Bing + DuckDuckGo 双引擎搜索，补充未收录到 GitHub 的官网
- **浏览器兜底**：静态页面未发现群入口时，自动用 Playwright 浏览器访问官网进行视觉扫描

### 3.3 入口提取（三层判定）

| 平台 | 入口类型 |
|------|---------|
| 微信 / 飞书 / Discord / QQ / 钉钉 | 二维码图片（已抓取） |
| QQ | QQ 群号（已提取） |
| 以上全部 | 链接（二维码暂未抓取成功） |

三层判定提取二维码：
1. **层1**：OpenCV QR 解码成功 + 内容含平台关键词 → 确认群二维码
2. **层2**：检测到 QR 特征但解码失败 + 文本含平台关键词 → 降级为链接候选
3. **层3**：无 QR 特征 + 尺寸≥400×400 + 文件≥20KB + 含平台意图 → 降级通过

### 3.4 入库管理

| 操作 | 语义 |
|------|------|
| 入库 | 加入已入库库，同时从后续搜索结果中排除 |
| 忽略 | 同样入库并排除，但默认不在普通入库列表中展示；可通过筛选恢复 |
| 标记入群 | 标记已成功加入的群，不影响搜索排除状态 |
| 移除 | 彻底删除记录，下次搜索可再次返回 |

入库列表支持按平台、已入群状态、忽略状态筛选。

### 3.5 全自动代理模式

输入关键词后点击「全自动代理模式」，系统从推荐词中随机抽取连续搜索，找到的所有群入口一键全部入库。

### 3.6 手动补录

对未自动发现的群入口，可通过「手动上传」表单提交：工具名、平台、群类型、入口类型（二维码图片或链接）。

### 3.7 搜索结果筛选

| 筛选项 | 说明 |
|------|------|
| 最低星级 | 过滤 GitHub stars 低于设定值的仓库 |
| 创建时间 | 过滤仓库创建时间范围 |
| 群聊平台 | 只显示特定平台的群入口（微信 / QQ / 飞书 / Discord） |
| 返回数量 | 滑条调节 3~50，默认 10 |

### 3.8 搜索缓存

- 每次搜索结果自动缓存到 SQLite（24 小时 TTL）
- 强制刷新（`refresh`）可绕过缓存重新搜索
- 已入库的群在结果中自动过滤排除

---

## 4. 数据模型

### 4.1 搜索结果

```typescript
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

OfficialGroup {
  groupId:    string   // SHA1(platform + entry + image)[:16]
  platform:   "微信" | "QQ" | "飞书" | "Discord" | "钉钉"
  groupType:  "交流群" | "答疑群" | "售后群" | "招募/内测群" | "未知"
  entry:      QRCodeEntry | LinkEntry | QQNumberEntry
  isAdded:    boolean   // 始终为 false（搜索结果不标记）
  sourceUrls: string[]
}

// 三种入口类型
QRCodeEntry    { type: "qrcode",    imagePath: string,  fallbackUrl?: string }
LinkEntry      { type: "link",      url: string }
QQNumberEntry  { type: "qq_number", qqNumber: string }
```

### 4.2 入库群

```typescript
ViewedGroup {
  viewKey:    string
  productId: string
  appName:    string
  platform:   Platform
  groupType:  GroupType
  entry:      GroupEntry
  viewedAt:   string      // 入库时间
  isJoined:   boolean    // 是否已入群
  isIgnored:  boolean    // 是否被忽略（不在默认列表中显示）
}
```

---

## 5. API 清单

| 方法       | 路径                                      | 说明                                 |
| -------- | --------------------------------------- | ---------------------------------- |
| `GET`    | `/api/health`                           | 健康检查（数据库路径、Chromium 就绪状态）          |
| `GET`    | `/api/recommendations`                  | 推荐词（GitHub topic:ai 热门，5 分钟缓存）     |
| `POST`   | `/api/search`                           | 搜索（支持 `filters`、`refresh`、`limit`） |
| `GET`    | `/api/groups/viewed`                    | 已入库群列表（含 `is_joined`、`is_ignored`） |
| `POST`   | `/api/groups/viewed`                    | 标记入库（`is_ignored: true` 时为忽略）      |
| `POST`   | `/api/groups/viewed/bulk`               | 批量入库                               |
| `PATCH`  | `/api/groups/viewed/{view_key}/joined`  | 切换已入群状态                            |
| `PATCH`  | `/api/groups/viewed/{view_key}/ignored` | 切换忽略状态                             |
| `DELETE` | `/api/groups/viewed/{view_key}`         | 从库中移除                              |
| `POST`   | `/api/groups/manual-upload`             | 手动补录群入口                            |

---

## 6. 成功标准

- 输入 `FastGPT` → FastGPT 相关结果靠前
- 输入 `ChatGPT` → 返回多个相关高星仓库
- GitHub README 或官网中存在微信群、QQ群、飞书群、Discord 二维码 → 尽量提取并展示
- 入库后该结果立刻不再出现在后续搜索中
- 忽略后该结果立刻不再出现在后续搜索中，且仅出现在"已忽略"筛选下

---

## 7. 非目标

- 不做付费外部搜索 API 接入
- 不做全网大规模二维码扫描
- 不做用户侧模式切换
- 不做批量忽略、忽略时间排序或额外统计

---

## 8. 已知限制

- GitHub API 匿名访问有频率限制，高频搜索可能触发限流
- 二维码提取依赖页面可见图片，私域邀请链接（需登录/扫码）无法抓取
- 钉钉当前主要通过链接文本匹配，覆盖范围有限
- 群类型（交流群/答疑群等）依赖上下文语义分析，当前全部标注为"未知"
