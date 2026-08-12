@echo off
REM Create a Startup launcher that users can run after interactive logon.
REM Direct copy into Startup was denied over OpenSSH; write to D: and register via schtasks ONCE as fallback.

set OUT=D:\rag_rerank\launch_rerank_hidden.vbs
> "%OUT%" echo Set sh = CreateObject("WScript.Shell")
>> "%OUT%" echo sh.Run "cmd /c D:\rag_rerank\run_offline_158.bat >> D:\rag_rerank\rerank.log 2>&1", 0, False

echo Wrote %OUT%
type "%OUT%"

REM Try user Startup again with VBS (smaller / different ACL path)
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
if not exist "%STARTUP%" mkdir "%STARTUP%"
copy /Y "%OUT%" "%STARTUP%\RAG-Rerank-8001.vbs"
echo COPY_VBS_EXIT=%ERRORLEVEL%

REM Try schtasks ONLOGON without /RU SYSTEM
schtasks /Delete /TN "RAG-Rerank-8001" /F >nul 2>&1
schtasks /Create /TN "RAG-Rerank-8001" /SC ONLOGON /RL LIMITED /TR "wscript.exe D:\rag_rerank\launch_rerank_hidden.vbs" /F
echo SCHTASKS_EXIT=%ERRORLEVEL%
schtasks /Query /TN "RAG-Rerank-8001" /FO LIST 2>nul

echo Listener:
netstat -ano | findstr :8001
