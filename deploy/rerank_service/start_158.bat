@echo off
setlocal
set RERANK_MODEL=D:\rag_rerank\models\bge-reranker-v2-m3
set RERANK_USE_FP16=false
cd /d D:\rag_rerank\app
D:\rag_rerank\venv\Scripts\uvicorn.exe server:app --host 0.0.0.0 --port 8001
