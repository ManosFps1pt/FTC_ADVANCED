@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "MCP_VENV=%CD%\mcp_server\.venv"
set "MCP_PYTHON=%MCP_VENV%\Scripts\python.exe"

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

echo Installing MCP dependencies...
"%MCP_PYTHON%" -m pip install -q -r "%CD%\mcp_server\requirements.txt"
if errorlevel 1 exit /b 1

echo.
echo Starting FTC Advanced MCP at http://127.0.0.1:8001/mcp
echo Keep this window open while ChatGPT uses the server. Press Ctrl+C to stop it.
"%MCP_PYTHON%" -m mcp_server
