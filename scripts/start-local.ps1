$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

# 后端优先使用项目虚拟环境，避免误用 PATH 上的其他 Python
$Python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }

# 后端用到 Python 3.11 才有的功能（如 enum.StrEnum、asyncio.TaskGroup）
& $Python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) {
  Write-Host '墨隐需要 Python 3.11 或更高版本：请从 https://www.python.org 安装最新的 Python 3，删除 backend\.venv 后重新运行。' -ForegroundColor Red
  exit 1
}

# 后端依赖有缺失时补装；都已安装时不联网，约一秒
& $Python -m pip install -q --disable-pip-version-check -r (Join-Path $Root 'backend\requirements.txt')
if ($LASTEXITCODE -ne 0) {
  Write-Host '后端依赖安装失败：请确认网络可用，然后重新运行。' -ForegroundColor Red
  exit 1
}

# 前端依赖有更新时先安装
$Frontend = Join-Path $Root 'frontend'
$Installed = Join-Path $Frontend 'node_modules\.package-lock.json'
if (-not (Test-Path $Installed) -or (Get-Item (Join-Path $Frontend 'package.json')).LastWriteTime -gt (Get-Item $Installed).LastWriteTime) {
  Write-Host '正在安装前端依赖……'
  Push-Location $Frontend
  npm install
  Pop-Location
}

Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoExit', '-Command', "Set-Location '$Root\backend'; & '$Python' -m uvicorn app.main:app --reload --port 8000"
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoExit', '-Command', "Set-Location '$Frontend'; npm run dev"
Write-Host '墨隐已启动：http://localhost:5173' -ForegroundColor Green
