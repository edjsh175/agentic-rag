Set-Location 'D:\rag_rerank\app'
& 'D:\rag_rerank\venv\Scripts\python.exe' '.\smoke_import.py'
if (Test-Path 'D:\rag_rerank\smoke_import.log') {
  Get-Content 'D:\rag_rerank\smoke_import.log'
}
