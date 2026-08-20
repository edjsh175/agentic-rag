# 博客已发布文章同步方案

## 1. 目标

将博客发布系统的已发布文章内容同步到 RAG 知识库，使发布的文章可被问答检索。

## 2. 数据流

```
定时触发 / 手动点击"同步"
       ↓
调用博客系统 API（GET /api/articles/all）
       ↓
返回 [{ id, title, contentMd }]
       ↓
对比 watch_directory/已发布文章/ 下的文件
       ↓
    ├── 本地没有该 id → 新建 {id}-{title}.md → 待入库
    ├── 本地有该 id，但标题或内容变了
    │     → 删除旧向量 → 覆盖文件 → 待入库
    ├── 本地有该 id，内容完全一致 → 跳过
    └── 本地存在文件但 API 无此 id（文章已删除）
          → 删除文件 → 删除向量
       ↓
触发 scanner.scan() 处理新增/变更文件（哈希去重）
```

## 3. 目录结构

```
watch_directory/
  ├── 已发布文章/       ← 博客系统同步过来的文章
  │   ├── 1-slug_title.md
  │   └── 2-slug_title.md
  ├── 博客文章/         ← 爬虫抓取的外部文章
  ├── 用户文档/         ← 上传的文档
  └── ...
```

## 4. 文件格式

每个文件以 `{id}-{slug(title)}.md` 命名，带 front-matter：

```markdown
---
title: 如何用 Python 实现 RAG
id: 1
synced_at: 2026-05-06 12:00:00
---

文章正文 markdown 内容...
```

front-matter 中的 `id` 用于匹配：文件重命名、内容变更均通过 id 追踪。

## 5. 配置

config.ini 新增段落：

```ini
[blog_publish]
api_url = http://localhost:8080/api/articles/all
sync_interval = 30
```

Config 类新增对应字段。

## 6. 同步服务

新文件 `rag_knowledge/services/blog_syncer.py`：

```
class BlogPostSyncer:
    - __init__(posts_dir, api_url, vector_store, scanner)
    - sync() → dict
      1. GET {api_url} → articles list
      2. 扫描本地 已发布文章/ 目录，按 id 建立索引
      3. 遍历 API 返回的文章：
         - 本地无此 id → 新建文件
         - 本地有此 id，内容相同 → 跳过
         - 本地有此 id，内容不同 → 删旧向量 → 覆盖文件
      4. 遍历本地文件，API 中已删除的 → 删文件 → 删向量
      5. 触发 scanner.scan()
      6. 返回统计 {new, updated, skipped, deleted}
```

## 7. API

```http
POST /blog/sync
→ { new: 3, updated: 1, skipped: 10, deleted: 0, message: "同步完成" }
```

## 8. 定时调度

在系统启动时（`__main__.py`）启动 BackgroundScheduler 定时任务，使用 `sync_interval` 配置分钟数。定时任务和手动触发共用同一个 `BlogPostSyncer.sync()` 方法。

## 9. 文件列表

| 文件 | 改动 |
|------|------|
| `config.ini` | 新增 `[blog_publish]` 段 |
| `rag_knowledge/config.py` | 新增配置字段 |
| `rag_knowledge/services/blog_syncer.py` | 新建 |
| `rag_knowledge/api/routes.py` | 新增 `POST /blog/sync` |
| `web/src/api/index.ts` | 新增 `syncPublishedPosts()` |
| `web/src/views/BlogView.vue` | 新增同步按钮 + 状态 |
