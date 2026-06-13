$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$DatabasePath = Join-Path $ProjectRoot "my_account_book.db"
$BackupDir = Join-Path $ProjectRoot "backups"

if (-not (Test-Path $DatabasePath)) {
    Write-Host "Database not found: $DatabasePath"
    exit 0
}

New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$BackupPath = Join-Path $BackupDir "my_account_book-$Timestamp.db"
Copy-Item -LiteralPath $DatabasePath -Destination $BackupPath
Write-Host "Database backup created: $BackupPath"
