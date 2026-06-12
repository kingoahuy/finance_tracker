$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$PythonwExe = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$RunnerScript = Join-Path $ProjectRoot "finance_tracker\service_runner.py"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunName = "FinanceTrackerServices"
$LegacyShortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "FinanceTrackerServices.lnk"
$Command = "`"$PythonwExe`" `"$RunnerScript`""

if (Test-Path $LegacyShortcut) {
    Remove-Item -LiteralPath $LegacyShortcut -Force
}

New-Item -Path $RunKey -Force | Out-Null
Set-ItemProperty -Path $RunKey -Name $RunName -Value $Command

Start-Process `
    -FilePath $PythonwExe `
    -ArgumentList "`"$RunnerScript`"" `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden

Write-Host "Installed and started windowless login startup: $RunName"
