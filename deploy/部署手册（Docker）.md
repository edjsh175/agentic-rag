## RAG 本地知识库问答系统 v2.0 —— 部署手册（按现网实操）

文档日期：2026-08-12
适用对象：本机开发机无 Docker → 虚拟机构建验收 → 公司服务器升级部署。

本手册按**已经跑通的真实链路**编写，不以理论双容器前端为准。

> **通用版（别人拿到源码在任意服务器部署）请看：** [`通用部署手册.md`](./通用部署手册.md)

---

### 0. 一句话结论

| 环境 | 角色 | 前端 | 后端 |
|---|---|---|---|
| 本机 Windows | 改代码、打 `web/dist`、中转文件 | 不跑生产入口 | 无 Docker |
| `192.168.137.141` | 构建/验收机（VMware Host-Only + ICS） | 可用 `rag-web` 容器 `:18080` 做验收 | `rag-service` `:10605` |
| `192.168.10.206` | **公司正式环境** | **宿主机 Nginx `:8004` SSL** + `/data/html/ragWeb` | **仅** `rag-service` `:10605` |

公司 **206 不上 `rag-web` 容器**。用户访问习惯是：

```text
浏览器 → https://<入口>:8004/（Nginx，server_name ragWeb）
         ├─ 静态站 /data/html/ragWeb
         └─ /api/ 反代 → http://192.168.10.206:10605/
```

Ollama（容器须可达）：`http://192.168.10.158:11434`
Rerank 独立服务（完全体 B）：`http://192.168.10.158:8001`
当前主模型示例：`qwen3-vl:8b`（见 `/data/rag_python/config.ini`）。

**完全体 B（现网已落地）** = 开图扩召回 + HttpRerank（调 158，不必在 206 镜像装 torch）+ `[graph_extraction.llm]` 走 158 Ollama。细节见 [`rerank_service/README.md`](./rerank_service/README.md)、[`config-206-aligned.ini`](./config-206-aligned.ini)。

---

## 1. 环境与账号（现网）

| 机器 | IP | 用途 | 备注 |
|---|---|---|---|
| 本机 | — | 源码、`npm run build`、pscp/plink 中转 | 无 Docker |
| 构建机 | `192.168.137.141` | `docker build` / 验收 | 旧 IP `192.168.10.141` 已废弃 |
| 公司服务器 | `192.168.10.206` | 正式服务 | root；Tomcat `8080`、RAG Nginx `8004` |
| Ollama | `192.168.10.158:11434` | 模型推理 | 禁止在 config 里写 `localhost` |
| Rerank | `192.168.10.158:8001` | HTTP Cross-Encoder | Windows；目录 `D:\rag_rerank\`；重启后可能需再拉起 |

本机常用工具：`plink` / `pscp`（PuTTY）。PowerShell 下远程命令里的 `$(date …)`、管道、复杂引号易被本地解析破坏，远程脚本尽量用单引号包住整段，或写成远端临时 `.sh`。

---

## 2. 架构对照（务必分清）

### 2.1 公司 206（正式）

```text
[用户浏览器]
      │ HTTPS :8004
      ▼
[宿主机 Nginx]  root=/data/html/ragWeb
      │
      ├─ /          → 静态 Vue dist
      └─ /api/      → proxy_pass http://192.168.10.206:10605/;
                          │
                          ▼
                   [Docker: rag-service]
                   镜像: rag-backend:cpu-http-reranker
                         （由 cpu-no-reranker + HttpReranker 客户端补丁）
                   挂载: /data/rag_python/{config.ini,data,chroma_db,logs,...}
                          │
                          ├─ Ollama  → 192.168.10.158:11434
                          └─ Rerank  → 192.168.10.158:8001  (type=http)
```

### 2.2 构建机 141（仅验收，可选 rag-web）

```text
浏览器 → :18080 → [rag-web 容器: static_server.py] → rag-service:10605
```

141 方案用于「没有公司 Nginx 时」验证前后端联调；**不得照搬到 206**。

### 2.3 仓库里相关文件的职责

| 文件 | 用途 |
|---|---|
| `Dockerfile` | 后端镜像（`INSTALL_RERANKER=false`） |
| `docker_entrypoint.py` | 可选启动包装（默认仍可用 `run.py`） |
| `docker-compose.yml` | 141 验收用（含 `rag-web` + `18080`）；206 可不依赖其前端段 |
| `web/Dockerfile` + `web/static_server.py` | 141 前端容器方案 |
| `web/dist/` | **206 正式前端产物**，覆盖到 `/data/html/ragWeb` |
| `deploy/config-206-aligned.ini` | 206 完全体 B 配置样例（开图 + HttpRerank + 抽图 LLM） |
| `deploy/rerank_service/` | 158 上 Rerank 服务与 206 补丁脚本说明 |
| `config-prod.ini` | 生产模板（偏保守；现网以 aligned / 宿主机 config.ini 为准） |

---

## 3. 公司 206 现网摸底清单（升级前必做）

SSH 登录后执行：

```bash
docker --version
docker compose version || ls -la /usr/libexec/docker/cli-plugins/docker-compose
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
ss -ltnp | grep -E '8004|10605|8080|80 '
ls -la /data/rag_python/
ls -la /data/html/ragWeb | head
curl -s http://127.0.0.1:10605/health
curl -sk https://127.0.0.1:8004/api/health
curl -s --max-time 3 http://192.168.10.158:11434/api/tags >/dev/null && echo ollama-ok
curl -s --max-time 3 http://192.168.10.158:8001/health && echo
```

已知现网事实（2026-08-12）：

- 有旧/新 `rag-service`，端口 **10605**；完全体 B 镜像标签示例 **`rag-backend:cpu-http-reranker`**
- **没有** `rag-web` 容器
- Nginx **8004 SSL**，`root /data/html/ragWeb`，`/api/` → `10605`
- 持久化在 `/data/rag_python/`
- Rerank：`192.168.10.158:8001`；容器 env `RERANKER_ENABLED=true`，config `type=http`
- `docker compose` 插件曾存在但无执行位，需 `chmod +x`
- compose 历史文件在 `/data/rag_python/docker-compose.yml`（可能仍写旧镜像名，以实际 `docker ps` 为准）

---

## 4. 完整升级流程（推荐：本机 → 141 → 本机中转 → 206）

整体步骤：

1. 本机构建前端 `dist`
2. （可选）141 构建并验收后端镜像
3. 141 `docker save` 后端镜像
4. 本机中转上传到 206（141 通常无 206 密码，不能直传）
5. 206：备份并覆盖静态站
6. 206：`docker load` → 停旧容器并 rename 保留 → 起新容器
7. 核对 / 更新 `config.ini` → `docker restart`
8. 验收与回滚预案

---

### 步骤 A：本机构建前端静态包

```powershell
cd "E:\申浩霖实习文件夹\rag_cy\rag\web"
npm ci
npm run build
```

确认存在 `web\dist\index.html` 与 `web\dist\assets\`。

打包便于上传：

```powershell
cd "E:\申浩霖实习文件夹\rag_cy\rag"
tar -czf ragWeb_dist.tar.gz -C web/dist .
```

建议同时准备策略文件（若 206 缺失）：

- `data/retrieval_intent_policies.json`
- `data/document_profile_map.json`

---

### 步骤 B：141 构建后端镜像（本机无 Docker）

1. 将后端构建所需源码放到 141 的 `/opt/rag`（含 `Dockerfile`、`requirements-*.txt`、`rag_knowledge/`、`run.py` 等）。
2. **不要**用空目录覆盖 141/206 的正式 `data/`、`chroma_db/`。
3. 构建：

```bash
cd /opt/rag
docker compose build rag-service --pull=false
# 或：docker build --build-arg INSTALL_RERANKER=false -t rag-backend:cpu-no-reranker .
# 源码已含 HttpReranker 时，也可直接：
# docker build --build-arg INSTALL_RERANKER=false -t rag-backend:cpu-http-reranker .
```

说明：

- 必须 `--pull=false` 或修好 Docker 代理，避免失效代理导致拉基础镜像失败。
- 前端容器构建仅用于 141 自测；**206 交付不需要 `rag-web` 镜像**。
- 完全体 B：导出/加载后镜像名可用 `cpu-http-reranker`（含客户端即可，仍 `INSTALL_RERANKER=false`）。

141 验收（可选）：

```bash
docker compose up -d
curl -s http://127.0.0.1:10605/health
curl -s http://127.0.0.1:18080/api/health   # 若起了 rag-web
```

---

### 步骤 C：导出后端镜像

在 **141**：

```bash
# 含 HttpReranker 的标签（完全体 B）优先：
docker save rag-backend:cpu-http-reranker -o /opt/rag-backend.tar
# 若尚未打该标签，也可用基础瘦镜像再在 206 上补丁：
# docker save rag-backend:cpu-no-reranker -o /opt/rag-backend.tar
ls -lh /opt/rag-backend.tar
```

在 **本机** 拉取（示例，hostkey 以实际为准）：

```powershell
pscp -batch -hostkey "SHA256:<141的指纹>" -pw <141密码> `
  root@192.168.137.141:/opt/rag-backend.tar `
  "E:\申浩霖实习文件夹\rag_cy\rag\rag-backend.tar"
```

再上传到 **206**：

```powershell
pscp -batch -pw <206密码> `
  "E:\申浩霖实习文件夹\rag_cy\rag\rag-backend.tar" `
  "E:\申浩霖实习文件夹\rag_cy\rag\ragWeb_dist.tar.gz" `
  "E:\申浩霖实习文件夹\rag_cy\rag\data\retrieval_intent_policies.json" `
  "E:\申浩霖实习文件夹\rag_cy\rag\data\document_profile_map.json" `
  root@192.168.10.206:/data/rag_python/
```

---

### 步骤 D：206 更新前端静态站（不改用户入口）

```bash
# 备份
cp -a /data/html/ragWeb /data/html/ragWeb.bak_YYYYMMDD_HHMMSS

# 解压新 dist 并覆盖（--delete 会去掉旧 assets 哈希文件）
mkdir -p /tmp/ragWeb_dist_new
rm -rf /tmp/ragWeb_dist_new/*
tar -xzf /data/rag_python/ragWeb_dist.tar.gz -C /tmp/ragWeb_dist_new
rsync -a --delete /tmp/ragWeb_dist_new/ /data/html/ragWeb/

ls -la /data/html/ragWeb
ls -la /data/html/ragWeb/assets | head
```

一般**不必**改 Nginx、也不必 reload；仅当改了 `nginx.conf` 才执行：

```bash
nginx -t && nginx -s reload
```

**禁止**在 206 上 `docker compose up rag-web` 抢端口或改变入口习惯。

---

### 步骤 E：206 替换后端容器

#### E1. 修复 compose 插件（若 `docker compose` 不可用）

```bash
chmod +x /usr/libexec/docker/cli-plugins/docker-compose
docker compose version
```

#### E2. 加载镜像

```bash
docker load -i /data/rag_python/rag-backend.tar
docker images | grep rag-backend
```

#### E3. 停旧并改名保留（便于回滚）

```bash
docker stop rag-service
docker rename rag-service rag-service_old_YYYYMMDD
```

#### E4. 按现网挂载启动新容器

以下挂载与 206 现网一致（含 `nltk_data`）：

**完全体 B（现网推荐）**——HttpRerank，**不必** `INSTALL_RERANKER=true`：

```bash
docker run -d \
  --name rag-service \
  --restart unless-stopped \
  -p 10605:10605 \
  -e RERANKER_ENABLED=true \
  -e PYTHONUNBUFFERED=1 \
  -v /data/apache-tomcat-9.0.89/webapps/zsltStaticData:/app/watch_directory \
  -v /data/rag_python/chroma_db:/app/chroma_db \
  -v /data/rag_python/logs:/app/logs \
  -v /data/rag_python/config.ini:/app/config.ini:ro \
  -v /data/rag_python/data:/app/data \
  -v /data/rag_python/scrape_article:/app/scrape_article \
  -v /data/rag_python/scrapingImages:/app/scrapingImages \
  -v /data/setup/nltk_data:/root/nltk_data \
  rag-backend:cpu-http-reranker \
  python run.py
```

说明：

- 镜像可由 `cpu-no-reranker` 叠加客户端补丁得到（见 `deploy/rerank_service/apply_206_http_rerank.sh`），或在构建机用含 `HttpReranker` 的源码直接 `docker build` 后打同名标签。
- 若暂时关 Rerank：改 config `[reranker] enabled=false`，并设 `-e RERANKER_ENABLED=false`，镜像可用 `cpu-no-reranker`。

等待约 20–40 秒（首次扫描可能稍慢）后再测健康检查。

#### E5. 补齐策略文件（若缺失）

```bash
cp -n /data/rag_python/retrieval_intent_policies.json /data/rag_python/data/ || true
cp -n /data/rag_python/document_profile_map.json /data/rag_python/data/ || true
```

---

### 步骤 F：配置（206）

配置文件路径：

```text
/data/rag_python/config.ini   → 容器内 /app/config.ini:ro
```

#### F1. 必查项

| 项 | 要求 |
|---|---|
| `[ollama] base_url` | `http://192.168.10.158:11434`（容器可达，禁止 localhost） |
| `[model] llm` | 按现网需要，例如 `qwen3-vl:8b` |
| `[reranker]` | 完全体 B：`enabled=true`，`type=http`，`base_url=http://192.168.10.158:8001`，`timeout=120`；容器 `-e RERANKER_ENABLED=true` |
| 路径类 | 保持 `/app/...` Docker 路径 |
| `[blog_publish]` | 保持指向 `192.168.10.206:8080` 的现网 API |

#### F2. 完全体 B 能力开关（现网已采用）

参考仓库 `deploy/config-206-aligned.ini`：

```ini
[reranker]
enabled = true
type = http
base_url = http://192.168.10.158:8001
timeout = 120
top_n = 8
candidate_k = 20

[graph_retrieval]
enabled = true
query_rewrite_enabled = true
anchor_chunk_filter_enabled = true
anchor_graph_chunk_enabled = true
max_graph_only_slots = 2
protect_text_top1 = true

[graph_extraction.llm]
enabled = true
provider = ollama
model = qwen3-vl:8b
# 206 无 Google 密钥；勿照搬本机 google provider
```

说明：

- HttpRerank **不要求** 206 镜像安装 FlagEmbedding/torch；只要镜像含 `HttpReranker` 客户端代码。
- CPU 版 158 Rerank 对长文档可能 >30s，故 `timeout` 建议 **120**。
- `query_rewrite_enabled` **仅当** `graph_retrieval.enabled=true` 时生效。
- Intent 评分（alias 等）不依赖 `graph_retrieval.enabled`。
- `graph_extraction.llm` 控制抽入库管线，不等于问答开关；试抽须遵守分拆审批 / apply 确认三件套。
- 改完配置必须：

```bash
docker restart rag-service
```

#### F3. 备份

```bash
cp -a /data/rag_python/config.ini /data/rag_python/config.ini.bak_YYYYMMDD
```

---

### 步骤 G：验收清单

```bash
docker ps --filter name=rag-service
curl -s http://127.0.0.1:10605/health
curl -sk https://127.0.0.1:8004/api/health
curl -sk -o /dev/null -w "%{http_code}\n" https://127.0.0.1:8004/
docker logs rag-service --tail 50
```

期望：

1. `health` 中 `status=ok`，且 `llm` / `embedding` / `vision` 与 config 一致
2. `8004` 前端 HTTP/HTTPS **200**
3. 浏览器打开正式入口 `https://<域名或IP>:8004/`，刷新子路由不 404
4. `curl http://192.168.10.158:8001/health` 返回 ok（完全体 B）
5. 发起一次问答；日志可见 `重排序器已启用: type=http`，且宜有 `POST http://192.168.10.158:8001/rerank` **200**（超时会降级原序，查 158 进程与 `timeout`）
6. 206 容器日志**不应**出现本地 FlagEmbedding/torch 加载（Http 模式无本地权重）

---

### 步骤 H：回滚

**后端：**

```bash
docker stop rag-service
docker rename rag-service rag-service_failed_YYYYMMDD
docker rename rag-service_old_YYYYMMDD rag-service
docker start rag-service
curl -s http://127.0.0.1:10605/health
```

**前端：**

```bash
rsync -a --delete /data/html/ragWeb.bak_YYYYMMDD_HHMMSS/ /data/html/ragWeb/
```

**配置：**

```bash
cp -a /data/rag_python/config.ini.bak_XXX /data/rag_python/config.ini
docker restart rag-service
```

---

## 5. 持久化目录约定（206）

`<BASE>=/data/rag_python`

| 宿主机 | 容器 |
|---|---|
| `<BASE>/config.ini` | `/app/config.ini:ro` |
| `<BASE>/data` | `/app/data` |
| `<BASE>/chroma_db` | `/app/chroma_db` |
| `<BASE>/logs` | `/app/logs` |
| `<BASE>/scrape_article` | `/app/scrape_article` |
| `<BASE>/scrapingImages` | `/app/scrapingImages` |
| `/data/apache-tomcat-9.0.89/webapps/zsltStaticData` | `/app/watch_directory` |
| `/data/setup/nltk_data` | `/root/nltk_data` |

前端静态（非 Docker）：

| 宿主机 | 说明 |
|---|---|
| `/data/html/ragWeb` | Nginx `root` |
| `/etc/nginx/nginx.conf` | `listen 8004 ssl;` / `location /api/` |

**严禁**用空 `data/` 覆盖正式库。最低建议文件：

- `rag_relational.db`
- `file_index.json`
- `document_profile_map.json`
- `retrieval_intent_policies.json`
- `domain_catalog.json`
- `agents.json`

---

## 6. 常见故障（实操踩过）

| 现象 | 原因 | 处理 |
|---|---|---|
| 本机 `sshpass` 不可用 | Windows 环境无该命令 | 用 `plink`/`pscp` |
| 141→206 `scp` Permission denied | 141 无 206 凭证 | 本机中转两段传输 |
| `docker compose` unknown / 权限不够 | 插件无 `+x` | `chmod +x .../docker-compose` |
| 构建拉基础镜像失败 | Docker HTTP 代理失效 | 关代理；`build --pull=false`；前端用预构建 dist |
| `rag-web` CMD 损坏反复重启 | Dockerfile CMD 字符串被写坏 | 仅 141 相关；206 不用该容器 |
| 容器 Up 但 `/health` 长时间不通 | 启动卡外连/首次扫描慢 | 看 `docker logs`；可试 `docker_entrypoint.py`；拉长等待 |
| 问答失败 | Ollama 不可达或模型名不存在 | 容器内访问 `192.168.10.158:11434`；确认 `api/tags` 有该模型 |
| `reranker failed ... timed out` | 158 未起 / CPU 过慢 / timeout 过小 | 查 `:8001/health`；必要时重启 `D:\rag_rerank\start_hkcu_158.bat`；config `timeout>=120` |
| 误以为要开 80/18080 | 与公司入口混淆 | 公司入口永远是 **8004** |
| PowerShell 远程 `$(date …)` 异常 | 本地展开 | 远程命令用单引号或写 `.sh` |

---

## 7. 与旧文档/仓库模板的差异

| 旧说法 | 现网实操 |
|---|---|
| 生产双容器，`rag-web` Nginx 镜像对外 80 | **206 无前端容器**；Nginx **8004** + `/data/html/ragWeb` |
| 公司也要 `docker save rag-web` | **只需** `rag-backend` |
| 生产默认关图 / 关 Rerank | 现网完全体 B：开图 + **HttpRerank→158** + 抽图 LLM@ollama；`config-prod.ini` 仍偏保守 |
| 开 Rerank 必须 `INSTALL_RERANKER=true` | 另有路径：`type=http` 调外部 `/rerank`，206 瘦镜像即可 |
| 构建机 `192.168.10.141` | 现为 **`192.168.137.141`** |
| 前端多阶段 npm 在服务器构建 | 本机/`141` 预构建 dist，206 只覆盖静态文件 |

简短说明仍见 [`deploy/README.md`](./README.md)；**以本文为正式操作手册**。

---

## 8. 交付签字清单

1. `https://<入口>:8004/` 可打开，静态资源 200
2. `https://<入口>:8004/api/health` 与 `http://127.0.0.1:10605/health` 均为 ok
3. `health` 中模型名与 config 一致（如 `qwen3-vl:8b`）
4. 旧容器已 rename 保留，或已明确可丢弃
5. 静态站与 config 均有带日期备份
6. 未覆盖空 `data/`；策略 JSON 齐全
7. 完全体 B：`RERANKER_ENABLED=true` + config `type=http`；158 `:8001/health` 可达（或已明确降级关 Rerank）
8. Ollama `192.168.10.158:11434` 与（若开 Rerank）`:8001` 容器网络可达

---

## 9. 快速命令索引（206 常用）

```bash
# 状态
docker ps -a --filter name=rag-service
curl -s http://127.0.0.1:10605/health
curl -sk https://127.0.0.1:8004/api/health
curl -s --max-time 3 http://192.168.10.158:8001/health

# 看配置关键开关
grep -nE 'base_url|^llm |^enabled|query_rewrite|anchor_|\[reranker\]|\[graph_' /data/rag_python/config.ini | head -60

# 改配置后
docker restart rag-service

# 日志（关注 HttpRerank）
docker logs rag-service --tail 100
docker logs rag-service 2>&1 | grep -iE '重排序|rerank|8001' | tail -30
tail -n 100 /data/rag_python/logs/rag.log
```
