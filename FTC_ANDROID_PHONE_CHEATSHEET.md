# FTC Android Robot Controller Phone Cheat Sheet

For an Android phone used as an FTC **Robot Controller (RC)**. Run commands from PowerShell in:

```powershell
cd C:\Users\Lefteris Dragasakis\Documents\GitHub\FTC_ADVANCED\FtcRobotController
```

> Enable Developer options and **USB debugging**, then approve the RSA prompt on the phone. Keep the robot safely supported or powered off while servicing the RC.

## Confirm the phone is connected

```powershell
adb devices -l
adb get-state
adb shell getprop ro.product.model
adb shell dumpsys battery | Select-String 'level|status|temperature'
```

The device state should be `device`. For `unauthorized`, unlock the phone and approve the prompt. For `offline`, reconnect USB, then run `adb kill-server; adb start-server`.

## Build and install Robot Controller

```powershell
# Build a debug APK
.\gradlew.bat :FtcRobotController:assembleDebug

# Install or update the app
adb install -r .\FtcRobotController\build\outputs\apk\debug\FtcRobotController-debug.apk

# Or: build and install in one command
.\gradlew.bat :FtcRobotController:installDebug
```

If installation reports a signing conflict, uninstall first. This removes the phone's RC app data and configuration:

```powershell
adb uninstall com.qualcomm.ftcrobotcontroller
adb install .\FtcRobotController\build\outputs\apk\debug\FtcRobotController-debug.apk
```

## Start, stop, and inspect the app

```powershell
# Launch Robot Controller
adb shell monkey -p com.qualcomm.ftcrobotcontroller 1

# Restart it
adb shell am force-stop com.qualcomm.ftcrobotcontroller
adb shell monkey -p com.qualcomm.ftcrobotcontroller 1

# Check whether it runs, and show installed version
adb shell pidof com.qualcomm.ftcrobotcontroller
adb shell dumpsys package com.qualcomm.ftcrobotcontroller | Select-String 'versionName|versionCode|codePath'
```

## Wi-Fi Direct recovery

First use the **RC app menu → Settings → Wi-Fi Direct settings**. Name the RC using your team format, for example `12345-RC`; use `12345-DS` on the Driver Station.

When pairing is stuck:

1. Stop the OpMode and safely power the robot down.
2. On both RC and DS, toggle Wi-Fi off and back on.
3. In the FTC apps' Wi-Fi Direct settings, disconnect/forget and reconnect the DS to the RC.
4. Reboot both phones if discovery or connection still fails.
5. As a last resort, use Android's **Reset Wi-Fi, mobile & Bluetooth**. This erases saved Wi-Fi networks and Bluetooth pairings; then recreate FTC Wi-Fi Direct pairing.

```powershell
# Regular Wi-Fi radio state (does not pair devices)
adb shell svc wifi enable
adb shell svc wifi disable

# Inspect Wi-Fi and Wi-Fi Direct/P2P state
adb shell dumpsys wifi
adb shell dumpsys wifi p2p

# Reboot the RC phone
adb reboot
```

> Do not routinely run `adb shell pm clear com.qualcomm.ftcrobotcontroller`: it resets RC app data, including local settings/configuration. Back up first if it is unavoidable.

## Logs and troubleshooting

```powershell
# Clear old logs, then watch FTC messages
adb logcat -c
adb logcat | Select-String -Pattern 'RobotLog|FtcRobotController|AndroidRuntime|Usb'

# Save a complete log snapshot locally
adb logcat -d -v threadtime > robot-controller-log.txt

# Inspect crashes and USB state
adb shell dumpsys activity exit-info com.qualcomm.ftcrobotcontroller
adb shell dumpsys usb

# Screenshot the phone
adb exec-out screencap -p > rc-screen.png
```

## Files and device controls

```powershell
# Storage and FTC files
adb shell df -h /sdcard
adb shell ls -la /sdcard/FIRST
adb pull /sdcard/FIRST .\phone-backup-FIRST

# Keep the display awake when USB-powered; restore normal timeout
adb shell settings put global stay_on_while_plugged_in 3
adb shell settings put global stay_on_while_plugged_in 0

# Wake / lock and identify device
adb shell input keyevent KEYCODE_WAKEUP
adb shell input keyevent KEYCODE_SLEEP
adb get-serialno
```

## Before practice or competition

- Match RC and DS app versions; run **Self Inspect** on both.
- Confirm matching team numbers and exact `-RC` / `-DS` Wi-Fi Direct names.
- Charge phones, disable airplane mode, and confirm the active robot configuration.
- Test motors only with the robot safely off the floor.
- Keep a known-good APK plus an exported log on the laptop.
