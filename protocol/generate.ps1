param([Parameter(Mandatory = $true)][string]$Protoc, [string]$Library = '../ftclogger')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$version = & $Protoc --version
if ($LASTEXITCODE -ne 0 -or $version -ne 'libprotoc 25.5') { throw 'Use protoc 3.25.5 (libprotoc 25.5) for reproducible bindings.' }
Push-Location $repoRoot
try {
    & python protocol/sync.py --library $Library --protoc $Protoc
    if ($LASTEXITCODE -ne 0) { throw 'Protocol generation failed' }
} finally { Pop-Location }
