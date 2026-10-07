@echo off
setlocal
cd /d "%~dp0"
title Moyin launcher

echo [1/4] Checking Node.js ...
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js was not found. Install Node.js 22 LTS from https://nodejs.org and run this file again.
  pause
  exit /b 1
)
node -e "const [a,b]=process.versions.node.split('.').map(Number);process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>=23?0:1)"
if errorlevel 1 (
  for /f %%v in ('node --version') do echo Node.js %%v is too old. Install Node.js 22 LTS from https://nodejs.org and run this file again.
  pause
  exit /b 1
)

echo [2/4] Checking the backend packages ...
rem pip only installs what is missing from backend\requirements.txt; when nothing is missing it finishes in about a second without network access
set "MOYIN_PIP_FLAGS=-q"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Creating backend\.venv ...
  py -3 -m venv backend\.venv || python -m venv backend\.venv
  set "MOYIN_PIP_FLAGS="
)
backend\.venv\Scripts\python.exe -m pip install %MOYIN_PIP_FLAGS% --disable-pip-version-check -r backend\requirements.txt
if errorlevel 1 (
  echo Could not install the packages listed in backend\requirements.txt.
  echo Check that Python 3.10 or newer is installed and the network is available, then run this file again.
  pause
  exit /b 1
)

echo [3/4] Checking frontend packages ...
if not exist "frontend\node_modules\@fontsource-variable\noto-serif-sc\package.json" (
  echo Installing frontend packages, this can take a few minutes ...
  pushd frontend
  call npm install
  popd
)

echo [4/4] Starting the backend and the frontend ...
start "Moyin backend 8000" /D "%~dp0backend" cmd /k .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
start "Moyin frontend 5173" /D "%~dp0frontend" cmd /k npm run dev -- --port 5173 --strictPort
timeout /t 8 /nobreak >nul
start "" "http://127.0.0.1:5173"
echo Moyin is running at http://127.0.0.1:5173
echo Keep the two service windows open. Close them to stop Moyin.
timeout /t 6 >nul
