param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
$PyInstaller = Join-Path $Root ".venv\Scripts\pyinstaller.exe"
$VersionFile = Join-Path $Root "bench_test\version.py"
$ExeName = "BenchTestController_v$Version"
$WatchdogName = "BenchTestWatchdog_v$Version"

if (-not (Test-Path $Python)) {
    throw "Python not found: $Python"
}

if (-not (Test-Path $PyInstaller)) {
    throw "PyInstaller not found: $PyInstaller"
}

$VersionContent = @"
APP_NAME = "Bench Test Controller"
APP_VERSION = "$Version"


def window_title(suffix: str = "") -> str:
    title = f"{APP_NAME} v{APP_VERSION}"
    if suffix:
        return f"{title}  {suffix}"
    return title
"@

Set-Content -Path $VersionFile -Value $VersionContent -Encoding UTF8

function Invoke-ReleaseBuild {
    $OriginalPath = $env:Path
    try {
        # Prefer Windows DLLs when tools on PATH ship libraries with the same name.
        $env:Path = "$env:SystemRoot\System32;$OriginalPath"
        & $PyInstaller @args
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    } finally {
        $env:Path = $OriginalPath
    }
}

Invoke-ReleaseBuild `
    --onefile `
    --windowed `
    --clean `
    -y `
    --name $ExeName `
    --icon "bench_test\ui\assets\app_icon.ico" `
    --add-data "bench_test\dropview\img;bench_test\dropview\img" `
    --add-data "bench_test\ui\assets;bench_test\ui\assets" `
    --exclude-module PIL._avif `
    main.py

Invoke-ReleaseBuild `
    --onefile `
    --windowed `
    --clean `
    -y `
    --name $WatchdogName `
    --exclude-module PIL._avif `
    watchdog_main.py

$OutputExe = Join-Path $Root "dist\$ExeName.exe"
$WatchdogExe = Join-Path $Root "dist\$WatchdogName.exe"
Write-Host "Build completed: $OutputExe"
Write-Host "Watchdog executable: $WatchdogExe"
