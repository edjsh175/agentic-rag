# 部署文档入口

按场景选一份即可，**不要混着看**。

| 文档 | 适用场景 |
|---|---|
| **[交接部署手册.md](./交接部署手册.md)** | **交接实施方按步骤完成首次部署**：从建目录、解包、改 IP、Ollama/Rerank、起容器，到用自有资料重建向量库（**不含 chroma 交接**） |
| [通用部署手册.md](./通用部署手册.md) | 技术人员拿到源码后，在任意服务器标准部署 |
| [部署手册（Docker）.md](./部署手册（Docker）.md) | 本项目现网实操：构建机 / 公司 `192.168.10.206`（Nginx:8004 等） |

交接配置样例：[`config-handover.sample.ini`](./config-handover.sample.ini)（把 `__OLLAMA_HOST__` / `__RERANK_HOST__` 换成算力机 IP）。

## 交接部署一句话

- 业务机：Docker `rag-service`（`:10605`）+ `rag-web`（`:18080`）
- 数据根：`/data/rag_python/`；文档放 `watch_directory/`；**空 `chroma_db` + 重建**
- 算力机：Ollama `:11434` + Rerank `:8001`（见 [`rerank_service/README.md`](./rerank_service/README.md)）
- 容器内配置禁止写 `localhost`

## 现网架构一句话（公司机，运维用）

- 后端：Docker `rag-service` → `10605`（完全体 B 镜像示例：`rag-backend:cpu-http-reranker`）
- 前端：宿主机 Nginx **`8004` SSL** + `/data/html/ragWeb`，`/api/` 反代到后端
- 配置/数据：`/data/rag_python/`
- Ollama / Rerank：`192.168.10.158`（`:11434` / `:8001`）
- 现网对齐样例：[`config-206-aligned.ini`](./config-206-aligned.ini)（**勿把内网 IP 原样交给外部**）

## 通用架构一句话

- 后端镜像默认：`rag-backend:cpu-no-reranker`（不装本地 Reranker 权重）
- 开精排二选一：① HttpRerank（`type=http` → 算力机 `:8001`）；② `INSTALL_RERANKER=true` 装本地模型
- 前端二选一：① `rag-web` 容器；② 宿主机 Nginx 托管 `web/dist`
- 保守模板：[`../config-prod.ini`](../config-prod.ini)

## 本机已打交接包（2026-08-13）

目录：`deploy/rag_handover_20260813/`
已含 `rerank_models/bge-reranker-v2-m3/`（自 158 拷贝）。
另有单文件：`E:\rag_handover_assets\bge-reranker-v2-m3.tar`。
按包内 `交接部署手册.md` + `MANIFEST.txt` 交付。不含 chroma / Ollama 权重。
