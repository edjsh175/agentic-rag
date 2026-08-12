@echo off
setlocal
echo === register Run key ===
reg add "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v RAGRerank8001 /t REG_SZ /d "D:\rag_rerank\run_offline_158.bat" /f
echo REG_EXIT=%ERRORLEVEL%

echo === kill old uvicorn on 8001 ===
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8001 ^| findstr LISTENING') do (
  echo kill PID %%p
  taskkill /F /PID %%p >nul 2>&1
)
ping -n 3 127.0.0.1 >nul

echo === WMI create detached process ===
wmic process call create "cmd.exe /c D:\rag_rerank\run_offline_158.bat >> D:\rag_rerank\rerank.log 2>&1"
echo WMIC_EXIT=%ERRORLEVEL%

ping -n 20 127.0.0.1 >nul
echo === netstat ===
netstat -ano | findstr :8001
echo === run key ===
reg query "HKLM\Software\Microsoft\Windows\CurrentVersion\Run" /v RAGRerank8001
echo === log tail ===
if exist D:\rag_rerank\rerank.log powershell -NoProfile -Command "Get-Content D:\rag_rerank\rerank.log -Tail 30"
