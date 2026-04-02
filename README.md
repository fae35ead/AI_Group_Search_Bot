# AI群聊发现器

AI群聊发现器是一个面向内部调研场景的本地工具，用于帮助研究人员快速发现 AI 产品在官网或 GitHub 官方页面中公开露出的官方群入口。

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

## 当前实现范围

- 已完成 Phase 1 基础工程
- 已跑通最小可用搜索闭环：
  - 输入产品名 / 常见别名 / 官网域名
  - 触发搜索
  - 返回官方群结果列表或明确空结果态
- 当前搜索范围：
  - 官网首页
  - 官网内带有 `community` / `contact` / `support` / `社群` / `群` 等语义的入口页
  - GitHub 官方仓库页
- 当前只支持 MVP 平台：微信 / QQ / 飞书

当前不包含：

- 登录体系
- 人工补录
- 团队共享状态
- 推荐关键词
- 钉钉 / Telegram / Discord

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

前端默认运行在 `http://127.0.0.1:5173`，开发阶段会将 `/api` 代理到本地后端。

### 2. 启动后端

```powershell
Set-Location .\app\backend
C:\Users\12279\AppData\Local\Programs\Python\Python311\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
uvicorn main:app --reload --port 8000
```

后端启动后会自动初始化本地 SQLite 数据库，并提供：

```text
GET /api/health
POST /api/search
```

## 怎么搜索

1. 在顶部搜索框输入产品名、常见别名或官网域名。
2. 按 Enter 或点击“搜索”。
3. 系统会实时抓取官网和 GitHub 官方页面。
4. 如果识别到官方群入口，页面会展示产品卡片和群信息。
5. 如果没有识别到官方群入口，页面会明确显示“未发现该产品的官方群”。

补充说明：

- 当前结果优先展示二维码；如果只有官方群链接，则会标注“二维码暂未抓取成功”。
- 当前版本不展示来源链接，不做“联系我们”或公众号二维码的替代展示。
- 当前版本没有引入“已添加”“筛选栏”“候选产品下拉”等后续交互。

## 当前限制

- 弱更新模式下，每次搜索都会重新抓取，部分搜索耗时可能超过 10 秒。
- 如果官方没有公开露出官方群入口，系统会直接返回空结果，不做补猜。
- GitHub stars 和创建时间只有在能定位到官方仓库时才会显示，否则分别显示 `—` 和 `未知`。
