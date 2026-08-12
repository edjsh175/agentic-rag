@echo off
REM Register as current user (no SYSTEM privilege required over SSH)
schtasks /Delete /TN "RAG-Rerank-8001" /F >nul 2>&1
schtasks /Create /TN "RAG-Rerank-8001" /SC ONLOGON /RL LIMITED /TR "cmd.exe /c D:\rag_rerank\run_offline_158.bat >> D:\rag_rerank\rerank.log 2>&1" /F
echo CREATE_EXIT=%ERRORLEVEL%
schtasks /Query /TN "RAG-Rerank-8001" /FO LIST
REM Kill existing listeners then run task
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8001 ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>&1
ping -n 3 127.0.0.1 >nul
schtasks /Run /TN "RAG-Rerank-8001"
echo RUN_EXIT=%ERRORLEVEL%
ping -n 20 127.0.0.1 >nul
schtasks /Query /TN "RAG-Rerank-8001" /FO LIST
netstat -ano | findstr :8001
echo ---- LOG TAIL ----
powershell -NoProfile -Command "if (Test-Path D:\rag_rerank\rerank.log) { Get-Content D:\rag_rerank\rerank.log -Tail 40 }"
