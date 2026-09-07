param([Parameter(Mandatory = $true)][string]$Protoc)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$version = & $Protoc --version
if ($LASTEXITCODE -ne 0 -or $version -ne 'libprotoc 25.5') { throw 'Use protoc 3.25.5 (libprotoc 25.5) for reproducible bindings.' }
Push-Location $repoRoot
try {
    & $Protoc --proto_path=protocol --python_out=web_driver_station/backend/protocol --java_out=lite:FtcRobotController/TeamCode/src/main/java protocol/robot_data.proto
    if ($LASTEXITCODE -ne 0) { throw 'Protocol generation failed' }
} finally { Pop-Location }
