# 代码修改记录

## 修改时间：2026-04-03

## ✅ 已完成的修改

### 1. 性能优化

#### 1.1 减少页面抓取数量
**文件：** `app/backend/app/search/service.py`
**修改：** `MAX_RELATED_LINKS = 6` → `MAX_RELATED_LINKS = 2`
**效果：** 每个候选项从最多 9 页减少到 5 页，预计节省 40% 抓取时间

#### 1.2 放宽图片宽高比限制
**文件：** `app/backend/app/search/entry_extractor.py`
**修改：** `ratio > 1.35` → `ratio > 1.5`（两处）
**效果：** 减少误过滤，提高 QR 码识别率

### 2. 安全加固

#### 2.1 添加图片下载大小限制
**文件：** `app/backend/app/search/entry_extractor.py`
**修改：** 在 `_download_image` 方法中添加 5MB 大小限制
**效果：** 防止恶意大文件攻击，避免内存耗尽

### 3. 错误处理改进

#### 3.1 GitHub API 限流明确提示
**文件：** `app/backend/app/search/service.py`
**修改：** 限流时抛出 HTTPStatusError 而不是返回空列表
**效果：** 用户能明确知道是 API 限流，而不是没有结果

## ⏳ 待完成的修改

### 高优先级（需要讨论）

#### 1. BeautifulSoup 对象缓存
**问题：** `FetchedPage` 是 frozen dataclass，无法直接添加缓存字段
**方案选项：**
- A. 改为非 frozen dataclass（可能影响其他代码）
- B. 使用单独的缓存字典（需要管理生命周期）
- C. 修改 `_collect_relevant_links` 接收 soup 参数

**请确认采用哪个方案？**

#### 2. 并行页面抓取（重要性能优化）
**问题：** 当前串行抓取页面，耗时最长
**方案：** 使用 asyncio + httpx.AsyncClient 并行抓取
**预计效果：** 150秒 → 30秒（节省 80%）
**工作量：** 需要重构 50 行代码

**是否立即实施？**

#### 3. Playwright 浏览器复用
**问题：** 每次搜索都启动新浏览器实例
**方案：** 使用浏览器池或长连接模式
**预计效果：** 节省 3 秒启动时间
**工作量：** 需要重构 30 行代码

**是否立即实施？**

### 中优先级（建议实施）

#### 4. API 请求限流
**位置：** `app/backend/app/api/routes.py`
**方案：** 使用 slowapi 添加限流中间件
**建议：** 每 IP 每分钟 20 次请求

#### 5. 前端 Error Boundary
**位置：** `app/frontend/src/App.tsx`
**方案：** 添加 React Error Boundary 组件
**效果：** 防止组件崩溃导致白屏

#### 6. 环境变量配置
**需要创建：** `.env.example` 文件

## 📊 性能提升预期

**当前状态：** ~176 秒（3 分钟）
**已完成优化后：** ~116 秒（2 分钟）- 节省 34%
**全部完成后：** ~15 秒 - 提速 91%

## 🔄 下一步行动

请确认以下问题：
1. BeautifulSoup 缓存采用哪个方案？
2. 是否立即实施并行页面抓取？
3. 是否立即实施 Playwright 浏览器复用？

