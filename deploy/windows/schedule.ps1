<#
.SYNOPSIS
    Register a Windows scheduled task that makes (and uploads) videos automatically.

.DESCRIPTION
    Creates the task "autoshorts" in Task Scheduler. At each time in -At it runs

        <repo>\.venv\Scripts\autoshorts.exe batch -n <Count> --upload <Upload>

    in the repo folder and appends the output to <repo>\output\scheduler.log.
    Run deploy\windows\install.ps1 first.

        powershell -ExecutionPolicy Bypass -File deploy\windows\schedule.ps1
        powershell -ExecutionPolicy Bypass -File deploy\windows\schedule.ps1 -At 09:00,18:00 -Upload youtube,tiktok
        powershell -ExecutionPolicy Bypass -File deploy\windows\schedule.ps1 -Remove

    Test it right away:  Start-ScheduledTask -TaskName autoshorts
    See it:              taskschd.msc (Task Scheduler Library -> autoshorts)

.PARAMETER At
    Daily start times (24-hour clock). Default: 08:00, 14:00, 20:00.

.PARAMETER Count
    Videos per run (default 1).

.PARAMETER Upload
    Platforms, e.g. "youtube" or "youtube,tiktok". Use "none" to only render.

.PARAMETER WhenLoggedOff
    Also run while you are signed out (S4U logon, no password stored). Needs an
    elevated (Run as administrator) PowerShell. By default the task runs only while
    you are signed in.

.PARAMETER TaskName
    Name of the scheduled task (default "autoshorts").

.PARAMETER Remove
    Delete the scheduled task instead of creating it.
#>
[CmdletBinding()]
param(
    [string[]]$At = @("08:00", "14:00", "20:00"),
    [ValidateRange(1, 50)][int]$Count = 1,
    [string]$Upload = "youtube",
    [switch]$WhenLoggedOff,
    [string]$TaskName = "autoshorts",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    } else {
        Write-Host "No scheduled task named '$TaskName'."
    }
    return
}

$exe = Join-Path $Repo ".venv\Scripts\autoshorts.exe"
if (-not (Test-Path $exe)) {
    throw "$exe not found. Run deploy\windows\install.ps1 first."
}
$outDir = Join-Path $Repo "output"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$logFile = Join-Path $outDir "scheduler.log"

$uploadArgs = "--upload $Upload"
if ($Upload -eq "none" -or $Upload -eq "") { $uploadArgs = "--no-upload" }

# Task Scheduler does not keep console output, so run autoshorts.exe through cmd.exe
# and append its output to a log file. cmd /c strips the outer pair of quotes.
$command = "`"$exe`" batch -n $Count $uploadArgs >> `"$logFile`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/d /c `"$command`"" -WorkingDirectory $Repo

$triggers = @()
foreach ($time in $At) {
    $triggers += New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($time.Trim(), [string[]]@("HH:mm", "H:mm"), [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::None))
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($WhenLoggedOff) {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
}

Register-ScheduledTask -TaskName $TaskName `
    -Description "autoshorts: make $Count short video(s) per run ($uploadArgs). Log: $logFile" `
    -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null

$when = ($At | ForEach-Object { $_.Trim() }) -join ", "
Write-Host "Scheduled task '$TaskName' registered: daily at $when, running as $user." -ForegroundColor Green
Write-Host "  command: autoshorts batch -n $Count $uploadArgs"
Write-Host "  log:     $logFile"
Write-Host "  test now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  remove:    powershell -ExecutionPolicy Bypass -File deploy\windows\schedule.ps1 -Remove"
if (-not $WhenLoggedOff) {
    Write-Host "  (runs only while you are signed in; use -WhenLoggedOff from an admin PowerShell to change that)"
}
