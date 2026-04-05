# ALPHA V4.6 SNIPER - DAILY SYNC PIPELINE
# Automation Script for Windows Task Scheduler (15:30 Daily)

$ErrorActionPreference = "Stop"

Write-Host "`n" + "="*60 -ForegroundColor Cyan
Write-Host "STARTING DAILY ALPHA SYNC (V4.6 SNIPER MODE)" -ForegroundColor Cyan
Write-Host "="*60 + "`n" -ForegroundColor Cyan

# 1. Update Macro & Index Data (Phase 0)
Write-Host "Step 1: Syncing Macro & Benchmarks..." -ForegroundColor Yellow
python seed_data.py --mode index

# 2. Update Top 300 Diamond Symbols (Phase 1)
# Recommendation: Re-scan 1Y for Top 300 to handle corporate actions/splits cleanly.
Write-Host "Step 2: Backfilling Top 300 Diamonds..." -ForegroundColor Yellow
python seed_data.py --mode diamonds

# 3. Generate Execution Orders (The Sniper)
Write-Host "Step 3: Generating Tactical Orders..." -ForegroundColor Yellow
python src/engine/execution_widget.py

Write-Host "`n" + "="*60 -ForegroundColor Green
Write-Host "DAILY SYNC COMPLETED SUCCESSFULLY" -ForegroundColor Green
Write-Host "Check 'orders_today.txt' for tactical commands." -ForegroundColor White
Write-Host "="*60 + "`n" -ForegroundColor Green
