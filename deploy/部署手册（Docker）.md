## RAG 本地知识库问答系统 v2.0 —— Docker 正式版部署手册

适用对象：需要将当前项目打包为 Docker 镜像，并部署到服务器提供服务（前端 Vue + Nginx + FastAPI 后端）。

目标系统组件：
1. `rag-service`：FastAPI 后端（CPU；生产默认关闭 Reranker）
2. `rag-web`：Nginx 托管前端 `web/dist/`，并将 `/api/` 代理到 `rag-service`

本手册严格基于项目仓库现有文件：`docker-compose.yml`、后端 `Dockerfile`、前端 `web/Dockerfile`、`web/nginx.conf`、`config-prod.ini`、`deploy/README.md`。

---

### 0. 一句话关键结论（先读这个）
- 生产部署必须使用：宿主机挂载的 `config.ini` 与 `data/`（不得空目录覆盖正式数据）。
- `rag-web` 只暴露 `80` 端口；`/api/` 请求会被 Nginx 代理到 `rag-service:10605`。
- 生产镜像默认不装 Reranker（`INSTALL_RERANKER=false` 且 `RERANKER_ENABLED=false`）。
- Ollama 地址（`[ollama] base_url`）必须是“容器网络可访问的 IP”，禁止 `localhost`。

---

## 1. 架构说明与端口映射

### 1.1 访问入口
- 浏览器访问：`http://<服务器IP或域名>/`
- API 健康检查（建议验收用）：`http://<服务器IP或域名>/api/health`

### 1.2 Docker 端口映射（以仓库 `docker-compose.yml` 为准）
- `rag-web`：容器 `80` 映射宿主机 `80`（`80:80`）
- `rag-service`：仅在 Docker 网络内暴露 `10605`（compose 使用 `expose`，不直接映射宿主机）

### 1.3 Nginx 代理规则（以仓库 `web/nginx.conf` 为准）
- `location /api/`：`proxy_pass http://rag-service:10605/;`
- `proxy_buffering off` / `proxy_cache off`：支持 SSE/流式输出不被缓冲影响

---

## 2. 部署前置条件

### 2.1 服务器环境
1. 安装并可用 Docker 与 Docker Compose
2. 服务器可访问 Ollama 服务（如果你开启/依赖 Ollama）
3. 必须准备好持久化目录（详见第 3 节）

### 2.2 网络与安全要点
- Ollama `base_url` 不能写成 `http://localhost:11434`（因为镜像内访问 localhost 指向自身容器，不会转发到宿主机 Ollama）。
- 生产只建议开启必要端口：
  - 仅对外暴露 `80`（或你服务器的实际网关策略）

---

## 3. 宿主机目录准备（必须）

`docker-compose.yml` 中的挂载路径决定了程序运行时的数据位置。首次部署前建议在服务器上提前创建这些目录（如不存在会导致挂载失败或运行时缺关键文件）。

### 3.1 `rag-service` 持久化挂载（以 compose 为准）
需要创建（以下用 `<BASE>` 代表你的持久化根目录，默认模板是 `/data/rag_python`）：
1. `<BASE>/chroma_db`  → `/app/chroma_db`
2. `<BASE>/logs`      → `/app/logs`
3. `<BASE>/config.ini`（文件）→ `/app/config.ini:ro`
4. `<BASE>/data`      → `/app/data`
5. `<BASE>/scrape_article` → `/app/scrape_article`
6. `<BASE>/scrapingImages` → `/app/scrapingImages`

此外还有一个 watch 目录（模板来自 compose）：
7. `/data/apache-tomcat-9.0.89/webapps/zsltStaticData` → `/app/watch_directory`

> 注意：`/app/watch_directory` 里的内容会影响入库/扫描流程；你必须确认它指向你希望被纳入知识库的文档目录。

### 3.2 `rag-web` 持久化挂载（以 compose 为准）
1. `<BASE>/scrapingImages`（只读）→ `/data/scrapingImages:ro`

---

## 4. 配置准备（config-prod.ini → config.ini）

### 4.1 配置文件来源
你仓库的生产模板是：`config-prod.ini`

正式部署规则：
- 将仓库根目录 `config-prod.ini` 复制到宿主机：
  - `/data/rag_python/config.ini`
- 容器内只读挂载为 `/app/config.ini:ro`

### 4.2 必填项与检查项

#### 4.2.1 `[ollama] base_url`
模板中示例是：
`base_url = http://192.168.10.158:11434`

你必须改成你的 Ollama 在“容器网络可访问”的地址。

禁止：
- `base_url = http://localhost:11434`

建议验证（在 compose 启动后执行，见第 9 节的验证命令）。

#### 4.2.2 `[reranker] enabled`
模板是 `enabled = false`。

并且你的后端镜像构建参数在 compose 中已经固定：
- `INSTALL_RERANKER: "false"`
- 运行环境变量 `RERANKER_ENABLED: "false"`

结论：
- 正式版不应启用 Reranker。

---

## 5. data 目录初始化（关键）

你的 `docker-compose.yml` 会把宿主机 `<BASE>/data` 完全覆盖容器内 `/app/data`。

这意味着：首次部署必须把必要文件初始化到宿主机的 `<BASE>/data`，否则系统行为可能与预期不一致（尤其是 Intent Policy、知识库一致性）。

### 5.1 最低建议集（以仓库手册为准）
至少包含：
- `rag_relational.db`
- `file_index.json`
- `document_profile_map.json`
- `retrieval_intent_policies.json`
- `domain_catalog.json`
- `agents.json`

### 5.2 不允许的动作
- 不允许用“空目录”覆盖正式 `data/`。
- 不建议先启动服务再“事后补齐 data”，因为可能已经初始化出错或触发缓存/索引不一致。

---

## 6. 构建镜像（在构建机执行一次）

在项目根目录（仓库根目录）执行：
```bash
docker compose build
```

构建结果（以 compose 为准）：
- 后端镜像：`rag-backend:cpu-no-reranker`
- 前端镜像：`rag-web`

---

## 7. 镜像发布到服务器（两种方式）

### 7.1 推荐方式：推送到镜像仓库（有公网或内网 registry 时）
1. 在构建机给镜像打上你的 registry tag
2. 执行 `docker push`
3. 服务器拉取后执行 compose 启动（见第 8 节）

> 你需要提供你的 registry 地址与 tag 规则，本手册不替你假设。

### 7.2 离线方式：docker save/load（适合无公网）
构建机导出：
```bash
docker save rag-backend:cpu-no-reranker -o rag-backend.tar
docker save rag-web -o rag-web.tar
```

传到服务器后导入：
```bash
docker load -i rag-backend.tar
docker load -i rag-web.tar
```

导入后确认镜像存在：
```bash
docker images
```

---

## 8. 服务器上启动服务

在服务器上，进入仓库目录（或你用于运行 compose 的目录），执行：
```bash
docker compose up -d
```

compose 中包含后端 healthcheck：
- `rag-service` 内部执行 `http://127.0.0.1:10605/health`
- healthcheck 通过后 `rag-web` 才会启动

---

## 9. 部署验收（必须做）

### 9.1 验收 A：后端健康检查
在服务器上执行（或直接通过 Nginx 访问）：
```bash
curl http://localhost/api/health
```

如果你是远程机器访问，请把 `localhost` 改成服务器 IP。

### 9.2 验收 B：Ollama 可达性（容器内）
按 `deploy/README.md` 的建议，在服务器上执行（注意：这是示例，IP 以你的配置为准）：
```bash
docker compose exec rag-service \
  python -c "import httpx; print(httpx.get('http://192.168.10.158:11434/api/tags').status_code)"
```

如果返回 `200`（或至少能连通），说明 Ollama 在容器网络中可达。

> 如果你不想在手册里写死 IP：请你把这条改成与 `config.ini` 里 `[ollama] base_url` 一致的地址。

### 9.3 验收 C：API 调用可用
测试：
- `POST /api/query`
- `POST /api/query/stream`（确认前端 SSE/流式不会异常）

> 这一步建议结合你项目现有前端页面进行实际问答验证（见 9.4）。

### 9.4 验收 D：前端可用
1. 打开：`http://<服务器IP或域名>/`
2. 刷新子路由不应出现 `404`

---

## 10. “正式无 reranker / 无 torch” 验收（建议）

你仓库明确写了生产默认要求：
- 不应加载 `FlagReranker`（如果你本地日志出现，通常意味着 Reranker 路径没按预期关闭）
- 不应发生 `torch` import 错误

建议验收方式（两类）：

### 10.1 依赖层面
在容器内检查（示例命令，你按实际环境替换）：
- `pip show torch`
- 查看运行日志中是否出现 torch/unstructured-inference 相关 import

### 10.2 镜像层面
在服务器上查看：
```bash
docker images
```

如果你有拆分镜像层策略，还可以进一步用 `docker history` 核对是否出现大块 torch/推理层（此处不强制）。

---

## 11. 常见故障排查（按症状定位）

### 11.1 前端能打开，但 `/api/health` 不通
可能原因：
- `rag-service` 未启动或健康检查失败
- Nginx 代理目标 `rag-service:10605` 不可达（网络或 compose 服务名不一致）

检查：
1. `docker compose ps`
2. `docker logs rag-service`（只读观察错误）
3. 确认 `web/nginx.conf` 中 `proxy_pass http://rag-service:10605/;` 没有被你改动

### 11.2 后端报 Ollama 连接失败
可能原因：
- `config.ini` 里的 `[ollama] base_url` 写成了 `localhost`
- Ollama 服务未运行或端口不通

检查：
1. 确认 Ollama 服务运行状态
2. 重新确认容器内访问地址（第 9.2 验收命令）

### 11.3 页面问答返回错误或内容为空
可能原因（与数据强相关）：
- `<BASE>/data` 关键文件未初始化或被空目录覆盖
- `file_index.json` 与 Chroma `chroma_db` 不一致
- `review_status` / 索引状态存在不匹配（需要进一步看日志）

检查：
1. `docker logs rag-service`
2. 检查 `<BASE>/data` 是否包含最低建议集文件

---

## 12. 升级与变更规范（避免“看似成功但数据不一致”）

### 12.1 代码升级（镜像更新）
- 建议每次升级都复做第 9 节验收清单。

### 12.2 配置升级（特别是模型/Embedding）
- 变更 embedding/向量模型通常要求重建知识库（重建会产生新的 chunk ID）。
- 生产手册中你已有受控重建方案（`/rebuild` 和 `RebuildCoordinator`），建议按项目现有方式走。

> 本手册不代替你执行重建流程；重建属于高风险操作，需按你项目的“受控重建”规范执行。

### 12.3 图谱增强开关（graph_retrieval）
如果你当前发布包只完成了向量库重建、尚未同步重建图谱，保持：
```ini
[graph_retrieval]
enabled = false
```

否则可能出现“新 Chunk ID + 旧图谱关联”，导致图谱增强检索命中旧链路。

---

## 13. 交付清单（建议作为签字版验收）

1. 服务可访问：
   - `http://<服务器IP或域名>/api/health` 返回正常
2. API 可用：
   - `/api/query` 正常返回
   - `/api/query/stream` SSE 正常
3. 前端可用：
   - `/` 正常加载，刷新不 404
4. 生产默认策略满足：
   - Reranker 关闭（`RERANKER_ENABLED=false` + `[reranker] enabled=false`）
5. 数据目录满足：
   - `<BASE>/data` 不为空目录覆盖，且包含最低建议集文件
6. Ollama 网络满足：
   - 容器内可连通 `[ollama] base_url`

---

## 14. 你需要在正式版里填的 4 个占位符

为避免手册“凭空写死 IP/目录”，建议你在部署时把以下信息统一填写到交付记录里：
1. `<服务器IP或域名>`
2. `<BASE>`（默认模板是 `/data/rag_python`，但你的服务器可能不同）
3. `[ollama] base_url` 的实际地址（容器网络可访问）
4. `/app/watch_directory` 对应的宿主机目录（compose 里当前是 Tomcat 的 `zsltStaticData`）

