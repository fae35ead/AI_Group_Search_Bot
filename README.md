# AI群聊发现器

AI群聊发现器是一个面向内部调研场景的本地工具，用于帮助研究人员快速发现 GitHub 上 AI 产品/项目的官方群入口。

当前仓库同时承载两类内容：

- `开发日志/`、`产品上下文/`、`OpenClaw/`：产品定义、约束与开发过程记录
- `app/frontend`、`app/backend`：当前 MVP 的实际代码

## 目录结构

```text
.
├── app/
│   ├── frontend/  # React + Vite + TypeScript
│   └── backend/   # FastAPI + SQLite + Playwright
├── 产品上下文/
├── 开发日志/
└── OpenClaw/
```

## 当前统一口径

当前版本按最简路径实现，核心流程只有四步：

1. 用户输入关键词或 GitHub repo URL
2. 后端调用 GitHub Search API 搜索相关仓库
3. 按 GitHub stars 为主排序返回相关结果
4. 从 GitHub 仓库页面中的图片与群入口链接提取二维码，并存入本地

当前不再以“先找官网、再找官方群”为主流程。

## 当前实现范围

- 已完成最小可用搜索闭环
- 当前搜索输入：
  - AI 工具名
  - 产品名
  - 宽泛关键词
  - 官网域名
  - GitHub repo URL
- 当前主要数据源：
  - GitHub Search API
  - GitHub 仓库主页（主链路）
  - 官网主页（补充链路，通过 Bing RSS 或 GitHub homepage 字段发现）
  - 官网相关内链（community / support / join 等）
- 当前返回字段：
  - 名称
  - 简介
  - GitHub stars
  - 创建时间
  - 最近验证时间
  - 群发现状态
  - GitHub 链接
  - 可展示的群二维码
- 当前支持群平台：
  - 微信
  - QQ
  - 飞书
  - Discord

当前不包含：

- 登录体系
- 人工补录
- 团队共享状态
- 付费推荐算法
- 全网网页二维码搜索

## 环境要求

```text
Node.js >= 20
Python >= 3.11
Playwright Chromium
SQLite（本地文件，无需单独安装服务）
```

## 怎么运行

### 1. 启动前端

```powershell
Set-Location .\app\frontend
npm install
npm run dev
```

前端默认运行在 `http://127.0.0.1:5173`，开发阶段会将 `/api` 和 `/assets` 代理到本地后端。

### 2. 启动后端

```powershell
Set-Location .\app\backend
.\.venv\Scripts\Activate.ps1
uvicorn main:app --port 8000 --reload
```

> 首次启动前需确认依赖已安装：
> - `pip install -r requirements.txt`
> - `python -m playwright install chromium`

后端启动后会自动初始化本地 SQLite 数据库，并提供：

```text
GET  /api/health
GET  /api/recommendations
POST /api/search
```

**接口说明：**
- `GET /api/recommendations`：返回 GitHub topic:ai 热门仓库排名前 12（经去噪过滤），带 30 分钟 TTL 内存缓存。用于首页推荐词入口。
- `POST /api/search`：搜索接口，传入 `{query: string}` 返回产品卡片列表。
- `GET /api/health`：健康检查。

## 怎么搜索

**入口A（推荐探索）：**
1. 打开首页，自动加载推荐关键词 chips（来自 `/api/recommendations`）。
2. 点击推荐词，复用主流搜索链路。

**入口B（主动搜索）：**
1. 在顶部搜索框输入关键词、产品名、官网域名或 GitHub repo URL。
2. 按 Enter 或点击”搜索”。
3. 后端调用 GitHub API 搜索相关仓库，并按 stars 排序。
4. 对返回的仓库抓取 GitHub 页面中的二维码图片和群链接。
5. 对返回的仓库逐个尝试提取微信/QQ/飞书/Discord 群入口。
6. 如果识别到群入口，页面展示产品卡片和二维码。
7. 如果没有识别到群入口，该仓库不进入结果展示。

补充说明：

- 当前结果优先展示二维码。
- 二维码图片会落到本地 `app/backend/data/public/qrcodes/`。
- 前端展示的二维码路径来自后端静态资源 `/assets/qrcodes/...`。
- 当前版本的目标是“先稳定出结果”，不是做复杂判定链路。

## 当前限制

- 当前搜索主要依赖 GitHub 匿名 API，请求过多时可能受限流影响。
- 当前二维码提取范围仍偏保守，主要依赖 GitHub 页面上直接可见的图片。
- 若仓库只提供邀请链接、不提供二维码，当前结果可能为空或需要继续补 link fallback。
- 当前版本还在快速收敛实现，优先保证“有结果”和“能显示图”，不追求复杂召回策略。
