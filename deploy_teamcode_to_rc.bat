@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Build and install the TeamCode debug app on one USB-connected Robot Controller.
rem Run this file from Explorer or from a Command Prompt in the repository root.

set "REPO_ROOT=%~dp0"
set "FTC_PROJECT=%REPO_ROOT%FtcRobotController"
set "GRADLE=%FTC_PROJECT%\gradlew.bat"
set "ADB="

if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
    set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
) else (
    for /f "delims=" %%A in ('where adb.exe 2^>nul') do (
        if not defined ADB set "ADB=%%A"
    )
)

if not defined ADB (
    echo ERROR: Android Debug Bridge ^(adb.exe^) was not found.
    echo Install Android Platform Tools or set up Android Studio's SDK.
    exit /b 1
)

if not exist "%GRADLE%" (
    echo ERROR: Could not find "%GRADLE%".
    exit /b 1
)

"%ADB%" start-server >nul
set /a DEVICE_COUNT=0
for /f "skip=1 tokens=1,2" %%A in ('"%ADB%" devices') do (
    if "%%B"=="device" (
        set /a DEVICE_COUNT+=1
        set "RC_SERIAL=%%A"
    )
)

if !DEVICE_COUNT! EQU 0 (
    echo ERROR: No authorized Robot Controller is connected by USB.
    echo Connect the RC, unlock it, and accept its USB-debugging prompt.
    "%ADB%" devices
    exit /b 1
)

if not !DEVICE_COUNT! EQU 1 (
    echo ERROR: Found !DEVICE_COUNT! authorized ADB devices. Connect only the target RC.
    "%ADB%" devices
    exit /b 1
)

echo Deploying TeamCode to Robot Controller !RC_SERIAL!...
pushd "%FTC_PROJECT%"
call "%GRADLE%" :TeamCode:installDebug
set "GRADLE_RESULT=!ERRORLEVEL!"
popd

if not "!GRADLE_RESULT!"=="0" (
    echo.
    echo DEPLOY FAILED. Gradle returned !GRADLE_RESULT!.
    exit /b !GRADLE_RESULT!
)

echo.
echo DEPLOY COMPLETE: TeamCode is installed on !RC_SERIAL!.
exit /b 0
