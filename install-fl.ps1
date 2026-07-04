# install-fl.ps1 - one-click setup for the MicroServiceFL localizer (Windows).
#
#   .\install-fl.ps1                    # uses `python` on PATH
#   .\install-fl.ps1 -Python C:\...\python.exe
#
# Sets up the Python runtime everything needs. JDK is only required to build the
# endpoint index / decompile; Maven only to build jars from source — both are
# checked (and warned about) but not installed here.

param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

Write-Host "== MicroServiceFL install ==" -ForegroundColor Cyan

# 1. venv
if (-not (Test-Path $VenvPy)) {
    Write-Host "creating venv ..." -ForegroundColor Yellow
    & $Python -m venv $Venv
}

# 2. install the package + fl extra (OpenHarness runtime + duckdb/pandas)
Write-Host "installing package (.[fl]) ..." -ForegroundColor Yellow
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -e "$Root[fl]"

# 3. CFR decompiler (grey-box root-cause refinement)
$Cfr = Join-Path $env:USERPROFILE "tools\cfr-0.152.jar"
if (-not (Test-Path $Cfr)) {
    Write-Host "downloading CFR decompiler ..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force (Split-Path $Cfr) | Out-Null
    try {
        Invoke-WebRequest -UseBasicParsing -OutFile $Cfr `
          -Uri "https://maven.aliyun.com/repository/public/org/benf/cfr/0.152/cfr-0.152.jar"
    } catch {
        Write-Host "  CFR download failed (optional) - decompile will degrade gracefully" -ForegroundColor Yellow
    }
}

# 4. environment check
Write-Host "`n== fl doctor ==" -ForegroundColor Cyan
& $VenvPy -m microservice_fl doctor

Write-Host "`nDone. Next:" -ForegroundColor Green
Write-Host "  # onboard your system (build the endpoint index from jars):"
Write-Host "  $VenvPy -m microservice_fl build-index --jars <your-jars-dir>"
Write-Host "  # then run (offline via DuckDB, or live via OH_FL_DATASOURCE=skywalking):"
Write-Host "  .\run_fl.ps1"
