@echo off
setlocal
set RERANK_MODEL=D:\rag_rerank\models\bge-reranker-v2-m3
set RERANK_USE_FP16=false
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set HF_HUB_DISABLE_TELEMETRY=1
cd /d D:\rag_rerank\app
D:\rag_rerank\venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8001
