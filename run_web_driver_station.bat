@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "WEB_VENV=%CD%\.venv-web"
set "WEB_PYTHON=%WEB_VENV%\Scripts\python.exe"
set "CODEX_RUNTIME=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies"

rem When launched from Explorer, Codex's bundled tools are not necessarily on PATH.
rem Put them first when available so Node.js and pnpm are always usable here.
if exist "%CODEX_RUNTIME%\node\bin\node.exe" (
    set "PATH=%CODEX_RUNTIME%\node\bin;%PATH%"
)

if exist "%CODEX_RUNTIME%\bin\fallback\pnpm.cmd" (
    set "PATH=%CODEX_RUNTIME%\bin\fallback;%PATH%"
)

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.11 or newer is required but was not found on PATH.
    echo Install Python, then run this file again.
    exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
    echo Node.js LTS is required but was not found.
    echo Install Node.js LTS, then run this file again.
    exit /b 1
)

where pnpm >nul 2>nul
if errorlevel 1 (
    echo pnpm is required but was not found on PATH.
    echo Run: corepack enable
    echo Then run this file again.
    exit /b 1
)

if not exist "%WEB_PYTHON%" (
    echo Creating the local Python environment...
    python -m venv "%WEB_VENV%"
    if errorlevel 1 exit /b 1
)

echo Installing Python dependencies...
"%WEB_PYTHON%" -m pip install -q -r "%CD%\web_driver_station\backend\requirements.txt"
if errorlevel 1 exit /b 1

echo Installing frontend dependencies...
pushd "%CD%\web_driver_station\frontend"
call pnpm install --frozen-lockfile
if errorlevel 1 (
    popd
    exit /b 1
)

echo Building the dashboard...
call pnpm run build
if errorlevel 1 (
    popd
    exit /b 1
)
popd

echo.
echo Starting the local Driver Station dashboard at http://127.0.0.1:8000
echo Keep this window open while using the dashboard. Press Ctrl+C to stop it.
start "FTC Local Driver Station" http://127.0.0.1:8000
"%WEB_PYTHON%" -m uvicorn web_driver_station.backend.main:app --host 127.0.0.1 --port 8000
