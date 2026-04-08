# ARCHITECTURE

## 总体结构
系统按三通道召回模型实现：

1. **官方产品通道**：精确产品名 → Bing RSS 官网站点发现 + GitHub homepage 字段匹配 → 优先召回官方产品
2. **GitHub 生态通道**：关键词 → GitHub Search API → 多个相关软件项目
3. **关键词推荐通道**：首页推荐 → 点击后复用 GitHub 生态通道

主流程统一为：候选工具召回 → 去噪/合并/排序 → 群入口发现 → 只返回有群的工具卡片。

## 固定流程

1. 标准化输入
2. 候选工具召回（按 §GitHub 搜索通道 所述三通道之一）
3. 候选工具去噪、合并、排序
4. 对前 10 个候选工具逐个执行群入口发现
5. 只返回已发现官方群入口的工具卡片

## 候选召回通道

### 官方产品通道
- 触发条件：用户输入精确产品名
- 行为：通过 Bing RSS 发现官网站点，结合 GitHub API homepage 字段匹配
- 目标：优先召回官方产品

### GitHub 生态通道（主流搜索）
- 触发条件：宽泛关键词或非精确产品名
- 行为：请求 GitHub `search/repositories`
- 查询词直接来自用户输入关键词
- 排序：`sort=stars`，`order=desc`

### 当前候选字段
- repo name
- description
- topics
- stars
- created_at
- updated_at / pushed_at
- homepage

### 当前过滤规则
- fork / archived / disabled 直接过滤
- 以下仓库降权或过滤：
  - `awesome`
  - `tutorial`
  - `guide`
  - `sdk`
  - `prompt`
  - `starter`
  - `boilerplate`

## 群入口发现范围

以 GitHub 仓库为主链路，官网发现为补充：

- **GitHub 仓库页面**（主链路）
  - 仓库主页
  - README 渲染内容
- **官网主页**（补充链路）
  - 官网域名由 Bing RSS 或 GitHub homepage 字段提供
- **官网相关内链**（补充链路）
  - community / support / join 等路径下的页面

> 不抓取 Discussions。

## 群入口发现边界

群入口发现覆盖范围（见 §群入口发现范围）：
- GitHub 仓库页面（主）
- 官网主页（补充）
- 官网相关内链（补充）

提取内容：
- 二维码图片
- 群入口链接

支持的平台：
- 微信
- QQ
- 飞书
- Discord

发现原则：
- 对品牌型产品：只认其官网 / 官方 GitHub
- 对 repo 型工具：该 repo README、项目主页、项目官网就是它自己的官方源
- 不从第三方帖子、导航站、论坛回帖或泛社区页面收录群入口
- 无群的工具不进入结果卡片列表

## API 语义

`POST /api/search`

响应：
- `results`: 仓库卡片数组
- `empty_message`: 仅在"没有召回到相关工具"或"召回到工具但未发现受支持官方群入口"时返回

卡片字段：
- `app_name`
- `description`
- `github_stars`
- `created_at`
- `verified_at`
- `group_discovery_status`
- `github_repo_url`
- `official_site_url`
- `groups`（仅在已发现群入口时返回，至少包含 1 条）

`GET /api/recommendations`

- 用途：返回关键词推荐，供首页探索入口使用
- 数据源：GitHub topic:ai 热门仓库排名前 12（经去噪过滤）
- 缓存：30 分钟 TTL 内存缓存
- 响应格式：`{tools: [{name, fullName, stars, description, topics}], cached_at}`

## 静态资源

二维码图片落盘目录：

```text
app/backend/data/public/qrcodes/
```

后端静态挂载：

```text
/assets/qrcodes/...
```

前端开发环境需要代理：
- `/api`
- `/assets`

## 当前关键约束

1. 只返回已发现官方群入口的工具卡片，无群的工具不展示。
2. 群入口只从候选工具自己的官方页面（GitHub 仓库 / 官网 / 官网内链）提取，不从第三方来源收录。
3. 前端筛选为客户端即时过滤，不重新请求后端。
4. 关键词推荐带 30 分钟 TTL，不实时拉取 GitHub。
