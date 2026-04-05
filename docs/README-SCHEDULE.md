# 🛡️ ALPHA V4.6 - Daily Scheduler Setup

To automate your Daily Sniper Sync at **15:30**, follow these steps:

### 1. Manual Setup (Task Scheduler GUI)
- Open **Task Scheduler**.
- Click **Create Basic Task**.
- Name: `Alpha_Sniper_Daily_Sync`.
- Trigger: **Daily** at **15:30**.
- Action: **Start a Program**.
- Program/script: `powershell.exe`
- Add arguments: `-ExecutionPolicy Bypass -File "e:\DEV\opensource_contrib\PTCK_VNSTOCK\scripts\alpha_sync_daily.ps1"`
- Start in: `e:\DEV\opensource_contrib\PTCK_VNSTOCK`

### 2. Quick CLI Setup (Elevated PowerShell)
Run the following in an Admin PowerShell terminal:

```powershell
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File 'e:\DEV\opensource_contrib\PTCK_VNSTOCK\scripts\alpha_sync_daily.ps1'" -WorkingDirectory "e:\DEV\opensource_contrib\PTCK_VNSTOCK"
$Trigger = New-ScheduledTaskTrigger -Daily -At 15:30
Register-ScheduledTask -Action $Action -Trigger $Trigger -TaskName "Alpha_Sniper_Daily_Sync" -Description "Daily sync of Alpha Forge V4.6"
```

---

### Verification
After the task runs, check for the updated `orders_today.txt` in the root folder.
If everything is correctly set up, the log should show **✅ DAILY SYNC COMPLETED SUCCESSFULLY**.
