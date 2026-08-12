# Quick probe for 158 Rerank (run locally against LAN)
$ErrorActionPreference = 'Continue'
try {
    $h = Invoke-RestMethod -Uri 'http://192.168.10.158:8001/health' -TimeoutSec 5
    "health=$($h | ConvertTo-Json -Compress)"
} catch {
    "health_fail=$_"
}
$body = '{"query":"StampServer","documents":["StampServer 服务端","无关"]}'
try {
    $r = Invoke-RestMethod -Uri 'http://192.168.10.158:8001/rerank' -Method Post `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec 60
    "rerank=$($r | ConvertTo-Json -Compress)"
} catch {
    "rerank_fail=$_"
}
