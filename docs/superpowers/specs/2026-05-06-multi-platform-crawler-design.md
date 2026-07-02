# 多平台博客爬虫设计方案

## 1. 目标

将现有的 CSDN 单平台爬虫扩展为支持多平台，爬取内容自动进入 RAG 知识库。

**支持平台：** CSDN、博客园、掘金、B站专栏、知乎专栏、微信公众号

## 2. 目录策略

```
watch_directory/
  ├── 博客文章/         ← blog_posts_dir = ./watch_directory/博客文章
  │   ├── 如何用Python实现RAG.md
  │   └── ...
  └── (其他用户上传的文档)
```

- `watch_directory` 不变，`posts_dir` 改为 `./watch_directory/博客文章`
- `watch_file_types` 加上 `md`
- `FileLoader.TEXT_EXTS` 加上 `.md`
- 爬取的文件存到 `watch_directory/博客文章/` 下
- 扫描器递归遍历时自动发现，`kb_name` 自然为"博客文章"

## 3. 爬虫架构

### 3.1 基类

```
BaseCrawler (抽象基类)
  ├── CSDNCrawler
  ├── JuejinCrawler
  ├── CnblogCrawler
  ├── BilibiliCrawler
  ├── ZhihuCrawler
  └── WechatCrawler
```

```python
class BaseCrawler(ABC):
    def __init__(self, posts_dir: Path):
        self.posts_dir = posts_dir

    @abstractmethod
    def crawl(self, url: str) -> dict:
        """返回 { title, source_url, author, platform, publish_date, markdown, file_path }"""
        ...
```

### 3.2 爬取策略

| 平台 | 方式 | 说明 |
|------|------|------|
| CSDN | HTML 解析 | 现有代码重构 |
| 博客园 | HTML 解析 | `#post_detail` / `#cnblogs_post_body` |
| 掘金 | API 调用 | SPA 页面，使用 content-api |
| B站专栏 | API 调用 | SPA 页面，使用 bilibili API |
| 知乎专栏 | HTML 解析 | 内容在 HTML 中直接渲染 |
| 微信公众号 | HTML 解析 | `#rich_media_content` |

### 3.3 Front-matter

每篇文章包含统一的 front-matter：

```yaml
---
title: xxx
author: xxx
source: https://...
platform: 掘金
crawled_at: 2026-05-06 10:00:00
publish_date: 2026-05-01
---
```

### 3.4 通用爬取流程

```
1. URL 验证（检查域名 + 格式）
2. HTTP GET 请求（带 User-Agent）
3. BeautifulSoup 解析 / API 调用来提取内容
4. 移除干扰元素（脚本、广告等）
5. 图片处理（提取 data-src → markdown 语法）
6. HTML → Markdown（html2text）
7. 组装 front-matter + markdown
8. 保存文件到 posts_dir
```

## 4. API 设计

### 4.1 统一爬取接口

```http
POST /crawl
Content-Type: application/json

{"url": "https://juejin.cn/post/xxxxx"}
```

响应：

```json
{
  "title": "xxx",
  "source_url": "https://...",
  "author": "xxx",
  "platform": "掘金",
  "publish_date": "2026-05-01",
  "file_path": "watch_directory/博客文章/xxx.md",
  "message": "文章已成功抓取并保存"
}
```

自动路由：后端根据 URL 域名自动选择对应爬虫。

保持不变：原 `GET /blog/posts` 和 `GET /blog/posts/{filename}`。

### 4.2 搜索 + 分页

列表接口增加参数：

```http
GET /blog/posts?page=1&page_size=20&q=关键词&platform=掘金
```

响应增加分页信息：

```json
{
  "total": 50,
  "page": 1,
  "page_size": 20,
  "total_pages": 3,
  "posts": [...],
  "posts_dir": "..."
}
```

- `q`: 标题模糊匹配（搜索）
- `platform`: 按平台筛选
- `page` / `page_size`: 分页
- 排序：按 `mtime` 倒序

### 4.3 后爬取自动入库

爬取完成后：
1. 保存 `.md` 文件到 `watch_directory/博客文章/`
2. 立即调用 `DirectoryScanner._process()` 处理该文件（或直接调用 `FileLoader.load()` + `VectorStore.add_chunks()`）
3. 用户无需手动触发扫描即可在知识库中搜索到

## 5. 前端改动

### 5.1 BlogView.vue

- 输入 URL → 统一调用 `crawl(url)` 接口
- 文章卡片显示平台标签（不同颜色区分）
- 列表顶部增加：
  - 搜索输入框（防抖 300ms）
  - 平台筛选下拉框
- 底部增加分页组件 + 每页条数选择（20/50/100）

### 5.2 api/index.ts

```typescript
// 统一爬取接口
export async function crawl(url: string) { ... }
// 删除旧的 crawlCsdn()

// 分页列表接口
export async function listBlogPosts(params: {
  page?: number, page_size?: number, q?: string, platform?: string
}) { ... }
```

### 5.3 types/index.ts

```typescript
export interface CrawlResult {
  title: string
  source_url: string
  author: string
  platform: string     // 新增
  publish_date: string | null
  file_path: string
  message: string
}

export interface BlogPostItem {
  filename: string
  title: string
  author: string | null
  platform: string | null  // 新增
  file_path: string
  file_size: number
  crawled_at: string | null
}

export interface BlogPostList {
  total: number
  page: number
  page_size: number
  total_pages: number  // 新增
  posts: BlogPostItem[]
  posts_dir: string
}
```

## 6. 改动文件清单

| 文件 | 改动 |
|------|------|
| `config.ini` | `posts_dir = ./watch_directory/博客文章`; `file_types` 加 `md` |
| `rag_knowledge/config.py` | 无改动（`blog_posts_dir` 从 config.ini 读取） |
| `rag_knowledge/services/loader.py` | `TEXT_EXTS` 加 `.md` |
| `rag_knowledge/services/blog_crawler.py` | 重构为 `BaseCrawler` + 各平台爬虫 |
| `rag_knowledge/services/scanner.py` | 无改动（用 watch_file_types 过滤） |
| `rag_knowledge/api/routes.py` | 新增 `POST /crawl` 统一路由，改造 `GET /blog/posts`  |
| `rag_knowledge/models/api.py` | `CrawlResponse` 加 `platform` 字段; `BlogPostItem` 加 `platform` 字段; 分页模型 |
| `web/src/api/index.ts` | 新增 `crawl()`, 更新 `listBlogPosts()` |
| `web/src/types/index.ts` | 更新 `CrawlResult`, `BlogPostItem`, `BlogPostList` |
| `web/src/views/BlogView.vue` | 搜索框 + 平台筛选 + 分页 + 平台标签 |
