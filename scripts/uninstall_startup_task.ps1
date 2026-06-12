$ErrorActionPreference = "Stop"

$TaskName = "FinanceTrackerServices"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunName = "FinanceTrackerServices"
$LegacyShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "FinanceTrackerServices.lnk"

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed legacy scheduled task: $TaskName"
}

Remove-ItemProperty -Path $RunKey -Name $RunName -ErrorAction SilentlyContinue

if (Test-Path $LegacyShortcut) {
    Remove-Item -LiteralPath $LegacyShortcut -Force
}

Write-Host "Removed Finance Tracker login startup."
