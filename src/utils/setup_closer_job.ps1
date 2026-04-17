
# setup_closer_job.ps1
# Thiết lập Windows Task Scheduler để tự động chạy Daily Closer vào lúc 16:00 mỗi ngày

$ActionScript = Join-Path $PSScriptRoot "..\src\daily_closer.py"
$PythonExe = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $ActionScript -WorkingDirectory (Get-Item $PSScriptRoot).Parent.FullName
$Trigger = New-ScheduledTaskTrigger -Daily -At 4pm
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName "PTCK_VNSTOCK_DailyCloser" -Action $Action -Trigger $Trigger -Settings $Settings -Description "Tự động cập nhật dữ liệu thị trường và Sentinel Alert của PTCK_VNSTOCK" -Force

Write-Host "✅ Đã đăng ký tác vụ tự động: PTCK_VNSTOCK_DailyCloser"
Write-Host "🕒 Thời gian thực thi: 16:00 hàng ngày."
Write-Host "📂 Thư mục làm việc: $((Get-Item $PSScriptRoot).Parent.FullName)"
