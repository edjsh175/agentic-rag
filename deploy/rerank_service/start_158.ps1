$env:RERANK_MODEL = 'D:\rag_rerank\models\bge-reranker-v2-m3'
$env:RERANK_USE_FP16 = 'false'

# kill old
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'uvicorn.*8001' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$cmd = 'set RERANK_MODEL=D:\rag_rerank\models\bge-reranker-v2-m3&& set RERANK_USE_FP16=false&& cd /d D:\rag_rerank\app&& D:\rag_rerank\venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8001 >> D:\rag_rerank\rerank.log 2>&1'
Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $cmd) -WindowStyle Hidden
Start-Sleep -Seconds 5
'launched detached' | Out-File 'D:\rag_rerank\rerank.pid.txt' -Encoding ascii
cmd /c 'netstat -ano | findstr 8001'
