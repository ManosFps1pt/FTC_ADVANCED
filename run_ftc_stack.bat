@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "STACK_SCRIPT=%~dp0run_ftc_stack.py"
if defined FTC_ADVANCED_PYTHON goto configured_python
if exist "%~dp0mcp_server\.venv\Scripts\python.exe" goto mcp_python

where py >nul 2>nul
if not errorlevel 1 goto python_launcher
where python >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.11+ or set FTC_ADVANCED_PYTHON. >&2
    exit /b 1
)
python -u "%STACK_SCRIPT%" %*
exit /b %ERRORLEVEL%

:configured_python
"%FTC_ADVANCED_PYTHON%" -u "%STACK_SCRIPT%" %*
exit /b %ERRORLEVEL%

:mcp_python
"%~dp0mcp_server\.venv\Scripts\python.exe" -u "%STACK_SCRIPT%" %*
exit /b %ERRORLEVEL%

:python_launcher
py -3 -u "%STACK_SCRIPT%" %*
exit /b %ERRORLEVEL%
