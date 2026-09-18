@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "MCP_VENV=%CD%\mcp_server\.venv"
set "MCP_PYTHON=%MCP_VENV%\Scripts\python.exe"
set "DISCORD_VENV=%CD%\discord_bot\.venv"
set "DISCORD_PYTHON=%DISCORD_VENV%\Scripts\python.exe"
set "TUNNEL_CLIENT=%CD%\mcp_server\tunnel-client\tunnel-client.exe"
set "TUNNEL_PROFILE=%CD%\mcp_server\tunnel-client\profiles\ftc-local.yaml"

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.11 or newer is required but was not found on PATH.
    exit /b 1
)

if not exist "%MCP_PYTHON%" (
    echo Creating the MCP Python environment...
    python -m venv "%MCP_VENV%"
    if errorlevel 1 exit /b 1
)

if not exist "%DISCORD_PYTHON%" (
    echo Creating the Discord bot Python environment...
    python -m venv "%DISCORD_VENV%"
    if errorlevel 1 exit /b 1
)

echo Installing MCP dependencies...
"%MCP_PYTHON%" -m pip install -q -r "%CD%\mcp_server\requirements.txt"
if errorlevel 1 exit /b 1

echo Installing Discord bot dependencies...
"%DISCORD_PYTHON%" -m pip install -q -r "%CD%\discord_bot\requirements.txt"
if errorlevel 1 exit /b 1

if not exist "%TUNNEL_CLIENT%" (
    echo The tunnel-client executable was not found:
    echo %TUNNEL_CLIENT%
    exit /b 1
)

if not exist "%TUNNEL_PROFILE%" (
    echo The tunnel profile was not found:
    echo %TUNNEL_PROFILE%
    exit /b 1
)

echo.
echo Starting FTC Advanced services in three windows:
echo   1. MCP server:     http://127.0.0.1:8001/mcp
echo   2. OpenAI tunnel:  ftc-local.yaml
echo   3. Discord bot:    slash commands for the configured test server
echo.
echo Start the Web Driver Station separately before using robot-data commands.
echo Close the three component windows to stop the services.

start "FTC Advanced MCP" /D "%CD%" "%MCP_PYTHON%" -m mcp_server

echo Waiting for the MCP listener before starting the tunnel...
set /a MCP_WAIT_SECONDS=0
:wait_for_mcp
powershell -NoProfile -Command "exit [int](-not (Test-NetConnection -ComputerName 127.0.0.1 -Port 8001 -InformationLevel Quiet))" >nul 2>nul
if not errorlevel 1 goto mcp_ready
set /a MCP_WAIT_SECONDS+=1
if %MCP_WAIT_SECONDS% GEQ 20 (
    echo MCP did not begin listening on port 8001 within 20 seconds.
    echo Check the FTC Advanced MCP window for its error, then close it before retrying.
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto wait_for_mcp

:mcp_ready
echo MCP listener is ready.
start "FTC Advanced MCP Tunnel" /D "%CD%" "%MCP_PYTHON%" "%CD%\ftc_local_config.py" run-tunnel "%TUNNEL_CLIENT%" run --profile-file "%TUNNEL_PROFILE%"
start "FTC Advanced Discord Bot" /D "%CD%" "%DISCORD_PYTHON%" -m discord_bot.bot
