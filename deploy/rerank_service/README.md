# GPU 机 Rerank 独立服务

与 RAG 侧 `type = http` + `base_url = http://<GPU主机>:8001` 配套。  
现网参考：`192.168.10.158:8001` ← `192.168.10.206` 容器 `rag-backend:cpu-http-reranker`。

## 协议

- `GET /health` → `{"status":"ok","model":"..."}`
- `POST /rerank`  
  请求：`{"query":"...","documents":["doc1","doc2"],"top_k":8}`  
  响应：`{"scores":[0.1, 0.9]}`（与 documents 等长）

## Windows GPU 机（158）落盘布局

```
D:\rag_rerank\
  Python311\          # 便携 Python 3.11+
  venv\               # 依赖环境（当前可为 CPU torch）
  models\bge-reranker-v2-m3\
  app\server.py
  run_offline_158.bat           # 设 RERANK_MODEL + HF 离线后起 uvicorn
  launch_rerank_hidden.vbs      # 无窗口拉起 bat
  start_hkcu_158.bat            # Start-Process 分离启动（SSH 下可用）
  install_startup_158.bat       # 尝试写入开机项（SSH 常因权限失败）
```

启动（推荐）：

```bat
D:\rag_rerank\start_hkcu_158.bat
```

或：

```bat
D:\rag_rerank\run_offline_158.bat
```

要点：

1. **必须** `RERANK_MODEL=D:\rag_rerank\models\bge-reranker-v2-m3`，并设 `HF_HUB_OFFLINE=1`，否则会去拉 HuggingFace。
2. 当前为 **CPU torch** 时可跑通；长文档 × `candidate_k=20` 可能 40s+，RAG 侧 `timeout` 建议 **120**。
3. OpenSSH 会话通常**无提权**：`schtasks` / `HKLM Run` / 用户 Startup 目录拷贝常「拒绝访问」。重启后需再执行一次 `start_hkcu_158.bat`，或 **RDP 提权**后注册计划任务 / 放入 Startup。
4. 防火墙放行 TCP **8001**（规则名示例：`RAG-Rerank-8001`）。

## RAG（206）配置

```ini
[reranker]
enabled = true
type = http
base_url = http://192.168.10.158:8001
timeout = 120
top_n = 8
candidate_k = 20
```

容器环境变量：`RERANKER_ENABLED=true`（覆盖曾写死的 false）。

镜像：在 `cpu-no-reranker` 上叠加 `HttpReranker` 客户端补丁即可，**不必** `INSTALL_RERANKER=true`。补丁脚本见同目录 `apply_206_http_rerank.sh` / `fix_206_http_rerank.sh`。

## 完全体 B 对照（现网）

| 能力 | 206 |
|---|---|
| Graph 扩召回 + 改写 | `[graph_retrieval] enabled=true` |
| HttpRerank → 158 | `[reranker] type=http` |
| LLM 图谱抽取 | `[graph_extraction.llm] enabled=true` + ollama@158 |
| 问答模型 | `qwen3-vl:8b`（Ollama @ 158） |
