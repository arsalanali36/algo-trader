# Registers the daily KHAZANA -> Google Drive backup as a Windows Scheduled Task.
# Runs 16:30 daily, in the interactive session (G: Drive mount needs logged-on user).
# Re-run this to update the schedule. Unregister:  Unregister-ScheduledTask -TaskName KHAZANA_Drive_Backup -Confirm:$false

$TaskName = 'KHAZANA_Drive_Backup'
$Script   = 'D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\_ops\drive_backup.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`""

# Two triggers: daily 16:30 (market close) + at logon (5-min delay so Google
# Drive has time to mount G: before the mirror runs). Incremental => harmless to
# run twice; MultipleInstances=IgnoreNew stops any overlap.
$trigDaily = New-ScheduledTaskTrigger -Daily -At 4:30PM
$trigLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigLogon.Delay = 'PT5M'
$trigger = @($trigDaily, $trigLogon)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description 'Mirror local trading data to G:\My Drive\KHAZANA_BACKUP (incremental + tar). Runs daily after market close.' `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName (daily 16:30 + at logon +5min)"
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
(Get-ScheduledTask -TaskName $TaskName).Triggers |
    Select-Object @{n='Type';e={$_.CimClass.CimClassName}}, StartBoundary, Delay
