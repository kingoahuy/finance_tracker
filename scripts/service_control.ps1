param(
    [ValidateSet("ensure", "status", "stop")]
    [string]$Action = "ensure"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$PythonwExe = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
$RunnerScript = Join-Path $ProjectRoot "finance_tracker\service_runner.py"
$LogDir = Join-Path $ProjectRoot "logs"
$RunnerPidFile = Join-Path $LogDir "service_runner.pid"
$StreamlitPidFile = Join-Path $LogDir "streamlit.pid"
$SchedulerPidFile = Join-Path $LogDir "scheduler.pid"
$FeishuBotPidFile = Join-Path $LogDir "feishu_bot.pid"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$RunName = "FinanceTrackerServices"

function Get-ProcessFromPidFile {
    param([string]$PidFile)

    if (-not (Test-Path $PidFile)) {
        return $null
    }

    $pidText = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pidText) {
        return $null
    }

    Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
}

function Test-StreamlitPort {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $result = $client.BeginConnect("127.0.0.1", 8501, $null, $null)
        $connected = $result.AsyncWaitHandle.WaitOne(2000, $false) -and $client.Connected
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Start-FinanceServices {
    $runner = Get-ProcessFromPidFile $RunnerPidFile
    if ($runner) {
        Write-Host "Finance services already running."
        return
    }

    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

    Start-Process `
        -FilePath $PythonwExe `
        -ArgumentList "`"$RunnerScript`"" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden

    Start-Sleep -Seconds 7
}

function Stop-PidFileProcess {
    param([string]$PidFile)

    $process = Get-ProcessFromPidFile $PidFile
    if ($process) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped process $($process.Id)"
    }
}

function Stop-FinanceServices {
    # Stop the supervisor first so it cannot respawn a child while shutdown is in progress.
    Stop-PidFileProcess $RunnerPidFile
    Start-Sleep -Milliseconds 500
    Stop-PidFileProcess $StreamlitPidFile
    Stop-PidFileProcess $SchedulerPidFile
    Stop-PidFileProcess $FeishuBotPidFile

    # Clean up stale project-owned children left behind by an older PID file.
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine.Contains($ProjectRoot) -and
        (
            $_.CommandLine.Contains("finance_tracker/app.py") -or
            $_.CommandLine.Contains("finance_tracker\scheduler.py") -or
            $_.CommandLine.Contains("finance_tracker\feishu_bot.py")
        )
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped stale project process $($_.ProcessId)"
    }
    Remove-Item -LiteralPath $RunnerPidFile, $StreamlitPidFile, $SchedulerPidFile, $FeishuBotPidFile -ErrorAction SilentlyContinue
}

function Show-FinanceStatus {
    $runner = Get-ProcessFromPidFile $RunnerPidFile
    $streamlit = Get-ProcessFromPidFile $StreamlitPidFile
    $scheduler = Get-ProcessFromPidFile $SchedulerPidFile
    $feishuBot = Get-ProcessFromPidFile $FeishuBotPidFile
    $startupValue = Get-ItemPropertyValue -Path $RunKey -Name $RunName -ErrorAction SilentlyContinue

    [PSCustomObject]@{
        StreamlitUrl = "http://127.0.0.1:8501"
        StreamlitPortListening = Test-StreamlitPort
        ServiceRunnerProcessId = if ($runner) { $runner.Id } else { "" }
        StreamlitProcessId = if ($streamlit) { $streamlit.Id } else { "" }
        SchedulerProcessId = if ($scheduler) { $scheduler.Id } else { "" }
        FeishuBotProcessId = if ($feishuBot) { $feishuBot.Id } else { "" }
        WindowlessStartupInstalled = [bool]$startupValue
        LogDirectory = $LogDir
    } | Format-List
}

if (-not (Test-Path $PythonwExe)) {
    throw "Pythonw not found: $PythonwExe"
}

if (-not (Test-Path $RunnerScript)) {
    throw "Service runner not found: $RunnerScript"
}

switch ($Action) {
    "ensure" {
        Start-FinanceServices
        Show-FinanceStatus
    }
    "status" {
        Show-FinanceStatus
    }
    "stop" {
        Stop-FinanceServices
        Show-FinanceStatus
    }
}
