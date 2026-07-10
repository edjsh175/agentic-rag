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
retrieval_intent_policies.json
domain_catalog.json
agents.json
```

```bash
ls -la /data/rag_python/data
```

**不得**用空目录覆盖已有正式 data，否则 Intent Policy / Graph 会静默降级。

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
4. 日志无 `FlagReranker` 加载、无 torch import 错误
5. `docker images` 确认镜像无 2.2GB 模型层
