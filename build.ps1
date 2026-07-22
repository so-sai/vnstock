param(
    [switch]$Dev,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "=== PTCK_VNSTOCK BUILD PIPELINE ===" -ForegroundColor Cyan
Write-Host "Pipeline: Nuitka (--onefile) -> Tauri -> NSIS installer" -ForegroundColor DarkGray
Write-Host ""

# Bước 0: Generate icon nếu chưa có
$IconFile = "$ProjectRoot/frontend/src-tauri/icons/app-icon.png"
if (-not (Test-Path $IconFile)) {
    Write-Host "[0/4] Generating app icon..." -ForegroundColor Yellow
    python "$ProjectRoot/backend/scripts/generate_app_icon.py"
    if ($LASTEXITCODE -eq 0) {
        Push-Location "$ProjectRoot/frontend"
        pnpm tauri icon src-tauri/icons/app-icon.png
        Pop-Location
    }
}

# Bước 1: Build Python sidecar (Nuitka --onefile)
Write-Host "[1/4] Building Python sidecar binary..." -ForegroundColor Yellow
$pyArgs = @("$ProjectRoot/backend/build_windows_installer.py")
if ($Dev) { $pyArgs += "--dev" }
if ($Clean) { $pyArgs += "--clean" }
python $pyArgs
if ($LASTEXITCODE -ne 0) { throw "Build failed" }

Write-Host ""
Write-Host "=== BUILD COMPLETE ===" -ForegroundColor Cyan
Write-Host "Installer: $ProjectRoot/frontend/src-tauri/target/release/bundle/nsis/" -ForegroundColor Green
