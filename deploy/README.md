# 部署文档入口

本目录有两份互补手册，请按场景选择：

| 文档 | 适用场景 |
|---|---|
| **[通用部署手册.md](./通用部署手册.md)** | 别人拿到源码后，在任意服务器从零/标准部署（占位符、双前端方案、验收清单） |
| **[部署手册（Docker）.md](./部署手册（Docker）.md)** | 本项目现网实操：构建机 `192.168.137.141`、公司 `192.168.10.206`（Nginx:8004 + 静态站，无 rag-web 容器） |

## 现网架构一句话（公司机）

- 后端：Docker `rag-service` → `10605`（完全体 B 镜像标签示例：`rag-backend:cpu-http-reranker`）
- 前端：宿主机 Nginx **`8004` SSL** + `/data/html/ragWeb`，`/api/` 反代到后端
- 配置/数据：`/data/rag_python/`
- Ollama / Rerank：`192.168.10.158`（`:11434` / `:8001`）；容器内禁止 `localhost`
- 完全体 B：`graph_retrieval` + HttpRerank + `graph_extraction.llm`（见 [`rerank_service/README.md`](./rerank_service/README.md)、[`config-206-aligned.ini`](./config-206-aligned.ini)）

## 通用架构一句话

- 后端镜像默认：`rag-backend:cpu-no-reranker`（不装本地 Reranker 权重）
- 开精排二选一：① HttpRerank（`type=http` → GPU 机 `:8001`）；② `INSTALL_RERANKER=true` 装本地模型
- 前端二选一：① `rag-web` 容器；② 宿主机 Nginx/Caddy 托管 `web/dist`
- 配置模板：仓库根目录 `config-prod.ini` → 宿主机 `<BASE>/config.ini`

## 配置样例

- 保守模板：[`../config-prod.ini`](../config-prod.ini)
- 某环境开图对齐样例：[`config-206-aligned.ini`](./config-206-aligned.ini)（**勿直接照抄内网 IP**）
- GPU 机 Rerank 独立服务：[`rerank_service/README.md`](./rerank_service/README.md)（RAG 侧 `type=http`）
