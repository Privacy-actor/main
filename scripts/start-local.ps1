$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoExit', '-Command', "Set-Location '$Root\backend'; python -m uvicorn app.main:app --reload --port 8000"
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoExit', '-Command', "Set-Location '$Root\frontend'; npm run dev"
Write-Host 'PrivShield 已启动： http://localhost:5173' -ForegroundColor Green
