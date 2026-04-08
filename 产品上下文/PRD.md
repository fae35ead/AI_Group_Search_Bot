## 产品定位
AI 群聊发现器当前定位为：`GitHub AI 仓库搜索 + 群入口发现`。

当前版本不再以官网发现为主，而是直接围绕 GitHub 搜索和 GitHub 页面提取来实现。

## 目标用户
- 需要快速搜集 AI 产品信息的调研人员
- 需要进入产品官方群的内部使用者

## 核心场景
1. 用户输入 `FastGPT`、`agent`、`RAG` 这类关键词。
2. 系统调用 GitHub Search API 搜索相关仓库。
3. 系统按 stars 排序返回多个相关结果。
4. 每张卡片显示仓库简介和群入口。
5. 若 GitHub 页面存在二维码，则展示本地保存后的二维码图片。若该产品不存在，则无需展示。

## 输入范围
- AI 工具名
- 产品名
- 宽泛关键词
- GitHub repo URL

## 输出规则
- 单次搜索最多返回 10 个仓库卡片。
- 每张卡片至少包含：
  - 名称
  - 简介
  - GitHub stars
  - 创建时间
  - 最近验证时间
  - 群发现状态
  - GitHub repo 链接
- 若发现群二维码：
  - 展示二维码图片
  - 图片已保存到本地
- `groups` 可以为空。
- `groups` 为空时，该卡片不进入结果列表（无群工具不展示）。

## 群入口边界
- 只收录微信 / QQ / 飞书 / Discord 官方群入口。
- 对品牌型产品：只认其官网 / 官方 GitHub。
- 对 repo 型工具：该 repo README、项目主页、项目官网就是它自己的官方源。
- 群入口发现范围覆盖：
  - GitHub 仓库页面（主链路）
  - 官网主页
  - 官网相关内链（community / support / join 等）
- 不从第三方帖子、导航站、论坛回帖或泛社区页面收录群入口。

## 候选仓库边界
以下对象可作为结果本体：
- GitHub 上维护中的 AI 软件项目
- GitHub 上与关键词强相关的 AI 产品仓库

以下对象应过滤或降权：
- awesome 列表
- 教程 / guide / tutorial
- 纯 SDK
- prompt 集合
- boilerplate / starter
- fork / archived / disabled

## 搜索语义
- 输入关键词后，系统直接搜索 GitHub。
- 排序优先以 stars 为主。
- 精确关键词应让官方或高相关仓库更靠前。
- 宽泛关键词应返回多个相关仓库，而不是只尝试解析单一官方产品。
- 前端支持对已有结果进行二次筛选（星级阈值、创建时间范围），筛选为客户端即时过滤，不重新请求后端。

## 成功标准
- 输入 `ChatGPT` 能返回多个相关高星仓库。
- 输入 `FastGPT` 时，FastGPT 结果靠前。
- 若 GitHub README 中存在微信群、QQ群、飞书群、Discord 二维码，应尽量提取并展示。
- 若没有群，无需保留相关仓库卡片。

## 非目标
- 不做付费外部搜索 API 接入。
- 不做全网搜二维码。
- 不做用户侧"模式切换"。
- 不在当前阶段强调钉钉、Telegram 支持。

## 关键词推荐系统
- 入口：用户打开首页时，后端返回关键词推荐列表。
- 数据源：GitHub topic:ai 热门仓库排名前 12（经去噪过滤）。
- API：`GET /api/recommendations`
  - 带 30 分钟 TTL 内存缓存
  - 响应格式：`{tools: [{name, fullName, stars, description, topics}], cached_at}`
- 交互：点击推荐词后，复用主流搜索链路（GitHub Search API → 候选工具 → 群入口发现）。
