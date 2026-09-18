@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "WEB_VENV=%CD%\.venv-web"
set "WEB_PYTHON=%WEB_VENV%\Scripts\python.exe"
set "CODEX_RUNTIME=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies"

rem Upload settings are loaded from the ignored ftc_advanced.local.json file.
rem Copy ftc_advanced.local.example.json and fill it locally before launching.

rem When launched from Codex, its bundled tools may be available already.
rem A normal Windows laptop instead bootstraps missing prerequisites with winget.
if exist "%CODEX_RUNTIME%\node\bin\node.exe" (
    set "PATH=%CODEX_RUNTIME%\node\bin;%PATH%"
)

if exist "%CODEX_RUNTIME%\bin\fallback\pnpm.cmd" (
    set "PATH=%CODEX_RUNTIME%\bin\fallback;%PATH%"
)

set "SYSTEM_PYTHON=python"
set "PNPM_COMMAND=pnpm"

where python >nul 2>nul
if errorlevel 1 (
    call :install_winget_package "Python.Python.3.12" "Python 3.12"
    if errorlevel 1 exit /b 1
    set "SYSTEM_PYTHON=py -3.12"
)

%SYSTEM_PYTHON% --version >nul 2>nul
if errorlevel 1 (
    echo Python was installed but is not available to this session yet.
    echo Close this window and run the launcher again.
    exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
    call :install_winget_package "OpenJS.NodeJS.LTS" "Node.js LTS"
    if errorlevel 1 exit /b 1
    set "PATH=%ProgramFiles%\nodejs;%PATH%"
)

where node >nul 2>nul
if errorlevel 1 (
    echo Node.js was installed but is not available to this session yet.
    echo Close this window and run the launcher again.
    exit /b 1
)

where pnpm >nul 2>nul
if errorlevel 1 (
    where corepack >nul 2>nul
    if errorlevel 1 (
        echo Node.js did not provide Corepack, which is needed to run pnpm.
        exit /b 1
    )
    set "PNPM_COMMAND=corepack pnpm"
    call %PNPM_COMMAND% --version >nul 2>nul
    if errorlevel 1 (
        echo Corepack could not prepare pnpm. Check your Internet connection and try again.
        exit /b 1
    )
)

where scrcpy >nul 2>nul
if errorlevel 1 (
    call :install_winget_package "Genymobile.scrcpy" "scrcpy camera capture"
    if errorlevel 1 exit /b 1
)

where scrcpy >nul 2>nul
if errorlevel 1 (
    echo scrcpy was installed but is not available to this session yet.
    echo Close this window and run the launcher again.
    exit /b 1
)

if not exist "%WEB_PYTHON%" (
    echo Creating the local Python environment...
    %SYSTEM_PYTHON% -m venv "%WEB_VENV%"
    if errorlevel 1 exit /b 1
)

echo Installing Python dependencies...
"%WEB_PYTHON%" -m pip install -q -r "%CD%\web_driver_station\backend\requirements.txt"
if errorlevel 1 exit /b 1

echo Installing frontend dependencies...
pushd "%CD%\web_driver_station\frontend"
call %PNPM_COMMAND% install --frozen-lockfile
if errorlevel 1 (
    popd
    exit /b 1
)

echo Building the dashboard...
call %PNPM_COMMAND% run build
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
exit /b %ERRORLEVEL%

:install_winget_package
where winget >nul 2>nul
if errorlevel 1 (
    echo winget is required to install %~2 automatically.
    echo Install App Installer from the Microsoft Store, then run this file again.
    exit /b 1
)
echo Installing %~2...
winget install --id %~1 --exact --silent --accept-package-agreements --accept-source-agreements
exit /b %ERRORLEVEL%
