@echo off
setlocal
echo === HKCU Run (no elevation needed) ===
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v RAGRerank8001 /t REG_SZ /d "D:\rag_rerank\run_offline_158.bat" /f
echo REG_EXIT=%ERRORLEVEL%
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v RAGRerank8001

echo === Start-Process Hidden ===
powershell -NoProfile -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c D:\rag_rerank\run_offline_158.bat >> D:\rag_rerank\rerank.log 2>&1' -WindowStyle Hidden -WorkingDirectory 'D:\rag_rerank\app'"
echo START_EXIT=%ERRORLEVEL%
ping -n 20 127.0.0.1 >nul
echo === netstat ===
netstat -ano | findstr :8001
echo === log tail ===
if exist D:\rag_rerank\rerank.log powershell -NoProfile -Command "Get-Content D:\rag_rerank\rerank.log -Tail 30"
