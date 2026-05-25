Param()

Set-StrictMode -Version Latest

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "[1/5] Cleaning previous build artifacts..."
Remove-Item -Path .\dist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path .\build -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path .\__pycache__ -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[2/5] Building frontend with Vite..."
Push-Location .\frontend
npm install
npm run build
Pop-Location

Write-Host "[3/5] Verifying frontend bundle..."
if (-not (Test-Path .\frontend\dist)) {
    Write-Error "Frontend dist folder is missing after build. Aborting."
    exit 1
}

Write-Host "[4/5] Installing PyInstaller in the active Python environment..."
$pythonExe = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Warning "Local backend virtual environment not found, falling back to system Python."
    $pythonExe = "python"
}
& $pythonExe -m pip install --upgrade pyinstaller | Write-Host

Write-Host "[5/5] Building standalone executable..."
& $pythonExe -m PyInstaller --clean .\sentinel_fortress.spec

Write-Host "Build complete. Output executable is in .\dist\Sentinel_Fortress_v1.0.exe"
