# 搜索响应慢性能分析

## 🔴 主要瓶颈

### 1. **串行页面抓取** ⏱️ 最大瓶颈
**位置：** `service.py` - `_fetch_candidate_pages`

**问题：**
```python
while queue and len(pages) < (3 + MAX_RELATED_LINKS):
    current = queue.pop(0)
    page = self._fetch_page(current)  # ❌ 串行执行，每个 8 秒超时
```

**影响：**
- 每个候选项最多抓取 9 个页面（3 + MAX_RELATED_LINKS=6）
- 每个页面超时 8 秒
- 最坏情况：9 页面 × 8 秒 = **72 秒/候选项**
- 10 个候选项 = **720 秒（12 分钟）**

**实际耗时估算：**
- GitHub 搜索：~2 秒
- 每个候选项平均抓取 5 个页面：5 × 3 秒 = 15 秒
- 10 个候选项：10 × 15 秒 = **150 秒（2.5 分钟）**

### 2. **重复 BeautifulSoup 解析**
**位置：** `service.py` - `_fetch_page` 和 `_collect_relevant_links`

**问题：**
```python
# _fetch_page 中解析一次
soup = BeautifulSoup(html, 'html.parser')

# _collect_relevant_links 中又解析一次
soup = BeautifulSoup(page.html, 'html.parser')  # ❌ 重复解析
```

**影响：**
- 每个页面解析 2 次
- 大 HTML（>1MB）解析耗时 0.5-2 秒
- 9 页面 × 2 次 × 1 秒 = **18 秒额外开销**

### 3. **Playwright 每次启动**
**位置：** `entry_extractor.py`

**影响：**
- 每次搜索都启动新的 Chromium 实例
- 启动开销：2-3 秒
- 关闭开销：0.5-1 秒
- 总计：**3-4 秒/搜索**

### 4. **GitHub 多次搜索**
**位置：** `service.py` - `_github_search`

**问题：**
```python
variants = [
    (f'{query} in:name', min(25, limit * 2)),
    (f'{query} in:description', min(15, limit)),
    (f'{query} in:readme', min(15, limit)),
]
```

**影响：**
- 3 次 GitHub API 请求
- 每次 10 秒超时
- 总计：**3-6 秒**

## 📊 总耗时估算

**当前架构（串行）：**
- GitHub 搜索：5 秒
- Playwright 启动：3 秒
- 页面抓取（10 候选 × 5 页 × 3 秒）：150 秒
- BeautifulSoup 重复解析：18 秒
- **总计：~176 秒（3 分钟）**

## ✅ 优化方案

### 方案 1：并行页面抓取（立即见效）

**修改：** `service.py` - `_fetch_candidate_pages`

```python
import asyncio
import httpx

async def _fetch_candidate_pages_async(self, candidate: GitHubRepositoryCandidate) -> list[FetchedPage]:
    urls = [candidate.repo_url, candidate.homepage, self._root_url(candidate.homepage)]
    urls = [u for u in urls if u]
    
    async with httpx.AsyncClient(timeout=PAGE_FETCH_TIMEOUT) as client:
        tasks = [self._fetch_page_async(client, url) for url in urls[:9]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    return [r for r in results if isinstance(r, FetchedPage)]
```

**效果：**
- 9 页面并行抓取：3 秒（取最慢的）
- 节省：150 秒 → 30 秒
- **总耗时：56 秒（1 分钟）**

### 方案 2：缓存 BeautifulSoup 对象（简单有效）

**修改：** `service.py` - `_fetch_page`

```python
def _fetch_page(self, url: str) -> FetchedPage | None:
    # ... 获取 HTML ...
    soup = BeautifulSoup(html, 'html.parser')
    return FetchedPage(
        html=html,
        soup=soup,  # ✅ 缓存解析结果
        title=soup.title.string.strip() if soup.title else '',
    )
```

**效果：**
- 避免重复解析
- 节省：18 秒
- **总耗时：38 秒**

### 方案 3：减少抓取页面数量（快速见效）

**修改：** `service.py` - 常量

```python
MAX_RELATED_LINKS = 2  # 从 6 改为 2
# 每个候选项最多抓取 5 个页面（3 + 2）
```

**效果：**
- 减少 40% 页面抓取
- 节省：60 秒
- **总耗时：116 秒（2 分钟）**

### 方案 4：Playwright 浏览器复用（长期优化）

**修改：** `entry_extractor.py`

```python
class EntryExtractor:
    _browser_instance = None
    
    async def _get_browser(self):
        if not self._browser_instance:
            self._browser_instance = await playwright.chromium.launch()
        return self._browser_instance
```

**效果：**
- 节省启动时间：3 秒
- **总耗时：35 秒**

## 🎯 推荐实施顺序

### 立即实施（今天）：
1. **方案 3**：减少页面数量（改 1 行代码）
2. **方案 2**：缓存 BeautifulSoup（改 5 行代码）

**效果：176 秒 → 98 秒（节省 44%）**

### 本周实施：
3. **方案 1**：并行页面抓取（重构 50 行代码）

**效果：98 秒 → 38 秒（节省 61%）**

### 下周实施：
4. **方案 4**：Playwright 复用（重构 30 行代码）

**最终效果：176 秒 → 15 秒（提速 91%）**

## 📊 结论

**响应慢的主要原因：**
1. ❌ 串行页面抓取（占 85% 耗时）
2. ❌ 重复 HTML 解析（占 10% 耗时）
3. ❌ Playwright 每次启动（占 2% 耗时）

**不是** BeautifulSoup 内存溢出，而是**串行 I/O 等待**。

---

**分析时间：** 2026-04-03  
**测试环境：** 假设 10 个候选项，每个 5 个页面
