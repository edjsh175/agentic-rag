# Persistent start via Scheduled Task (survives SSH disconnect / reboot)
$ErrorActionPreference = 'Stop'

Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'uvicorn.*8001' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

Unregister-ScheduledTask -TaskName 'RAG-Rerank-8001' -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
  -Execute 'cmd.exe' `
  -Argument '/c D:\rag_rerank\run_offline_158.bat >> D:\rag_rerank\rerank.log 2>&1'
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
  -TaskName 'RAG-Rerank-8001' `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Settings $settings `
  -Force | Out-Null

Start-ScheduledTask -TaskName 'RAG-Rerank-8001'
Start-Sleep -Seconds 15
Get-ScheduledTask -TaskName 'RAG-Rerank-8001' | Format-List TaskName, State
Get-ScheduledTaskInfo -TaskName 'RAG-Rerank-8001' | Format-List LastRunTime, LastTaskResult
cmd /c 'netstat -ano | findstr :8001'
Get-Content D:\rag_rerank\rerank.log -Tail 30 -ErrorAction SilentlyContinue
