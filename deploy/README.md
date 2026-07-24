# Docker 生产部署说明

## 架构

- `rag-service`：FastAPI 后端（CPU、无 Reranker 依赖）
- `rag-web`：Nginx 托管 Vue `dist/`，代理 `/api/` 到后端

## 首次部署前检查

### 1. 配置文件

将仓库 [`config-prod.ini`](../config-prod.ini) 复制到宿主机：

```text
/data/rag_python/config.ini
```

确认 `[reranker] enabled = false`。容器内只读取 `/app/config.ini`（volume 挂载）。

### 2. data 目录（关键）

Compose 挂载 `/data/rag_python/data:/app/data` 会**完全覆盖**镜像内 `data/`。
首次部署须将项目 `data/` 中必要文件初始化到宿主机，至少包括：

```text
rag_relational.db
file_index.json
document_profile_map.json
retrieval_intent_policies.json
domain_catalog.json
agents.json
```

```bash
ls -la /data/rag_python/data
```

**不得**用空目录覆盖已有正式 data，否则 Intent Policy / Graph 会静默降级。

### 2.1 图谱检索开关

如果当前发布包只完成了向量库重建、尚未同步重建图谱，请保持：

```ini
[graph_retrieval]
enabled = false
```

否则会出现“新 Chunk ID + 旧图谱关联”混用，导致图谱增强检索命中旧链路。

### 3. Ollama 地址

`config.ini` 中 `base_url` 须使用容器网络可访问的 IP，例如：

```ini
base_url = http://192.168.10.158:11434
```

禁止使用 `localhost`（容器内指向自身）。

验收：

```bash
docker compose exec rag-service \
  python -c "import httpx; print(httpx.get('http://192.168.10.158:11434/api/tags').status_code)"
```

### 4. Reranker 模型（本期关闭）

生产 `INSTALL_RERANKER=false`，无需挂载 `models/`。若将来启用，单独挂载：

```text
/data/rag_python/models/bge-reranker-v2-m3:/app/models/bge-reranker-v2-m3:ro
```

### 5. 依赖拆分：两条互不替代的「torch 口子」

清单拆分只门控 **Reranker**；业务 base 里另有一条曾未门控的深度学习依赖，二者不要混为一谈。

| 路径 | 控制方式 | 内容 | 生产 CPU 默认 |
|------|----------|------|----------------|
| **A. Reranker** | Dockerfile `ARG INSTALL_RERANKER`（默认 `false`）+ `requirements-reranker.txt` | `torch` / FlagEmbedding / transformers | **不装** |
| **B. 文档解析 extras** | `requirements-base.txt` 是否写 `unstructured[pdf]` | `[pdf]` → `unstructured-inference` → **torch** 等 | **禁止**；现用 `unstructured==0.18.32`（仅 md/txt 分区） |

说明：

- `INSTALL_RERANKER=false` **不能**挡住路径 B；旧 base 写 `unstructured[pdf,...]` 时，即便关掉 Reranker，镜像仍会又大又慢（甚至 pip 回退失败）。
- 运行时 PDF/DOCX **不**依赖 unstructured 版面推理：PDF 走 PyMuPDF/pypdf，DOCX 走 python-docx；unstructured 只用于 `.md`/`.txt` 章节切片。
- 验收「无 torch」时：路径 A 关 + 路径 B 未引入 `[pdf]`，二者都满足才算 CPU 瘦镜像。

相关文件：[`requirements-base.txt`](../requirements-base.txt)、[`requirements-reranker.txt`](../requirements-reranker.txt)、[`Dockerfile`](../Dockerfile)。

## 构建与启动

```bash
docker compose build
docker compose up -d
```

后端 healthcheck 通过后 `rag-web` 才会启动。

## 验收清单

1. `curl http://localhost/api/health` 或经 Nginx 访问 `/api/health`
2. `POST /api/query`、`/api/query/stream` 正常
3. 前端 `/` 加载，刷新子路由不 404
4. 日志无 `FlagReranker` 加载、无 torch import 错误（路径 A 关闭）
5. `docker images` 确认镜像无 Reranker 模型层；`pip show torch` / `unstructured-inference` 在容器内应不存在（路径 A + B 均未引入）
6. `requirements-base.txt` 未使用 `unstructured[pdf]`（路径 B）
