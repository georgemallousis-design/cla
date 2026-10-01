<#
.SYNOPSIS
    Register a Windows scheduled task that makes (and optionally uploads) videos automatically.

.DESCRIPTION
    Creates the task "autoshorts" in Task Scheduler. At each time in -At it runs

        <repo>\.venv\Scripts\autoshorts.exe batch -n <Count> --no-upload
        (or --upload <Upload> when -Upload is given)

    in the repo folder, in a hidden window, and appends the output to
    <repo>\output\scheduler.log. Run deploy\windows\install.ps1 first.

    By default the videos are only made, not uploaded: until your Google API project
    passes YouTube's audit, API uploads are locked private for good (README, "YouTube
    upload setup"). Review the videos in output\ and upload the good ones with
    "autoshorts upload <folder> --to youtube". Once the project is audited, register the
    task again with -Upload youtube (or youtube,tiktok).

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
    Platforms to upload to, e.g. youtube or youtube,tiktok. Default "none": only render.

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
    [string[]]$Upload = @("none"),
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

# "-Upload youtube,tiktok" arrives as one string with "powershell -File", but as an array
# when the script is run inside PowerShell (.\schedule.ps1 or &); [string] would join that
# array with spaces ("youtube tiktok") and every run would fail on the extra word.
$Upload = (@($Upload | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ",")
if (-not $Upload -or $Upload -eq "none") {
    $uploadArgs = "--no-upload"
} else {
    $uploadArgs = "--upload $Upload"
}

# Task Scheduler does not keep console output, so autoshorts.exe runs through cmd.exe,
# which appends its output to a log file. cmd /c strips the outer pair of quotes.
$command = "`"$exe`" batch -n $Count $uploadArgs >> `"$logFile`" 2>&1"
# A task that starts cmd.exe directly opens a console window on the desktop for the
# whole run, and closing it kills a render or upload half-way. A tiny WSH launcher
# starts cmd.exe hidden (window style 0), waits for it and returns its exit code.
$launcher = Join-Path $outDir "run-autoshorts.vbs"
$cmdLine = "cmd.exe /d /c `"$command`""
$vbs = @(
    "' Written by deploy\windows\schedule.ps1: runs autoshorts in a hidden window.",
    "Set shell = CreateObject(`"WScript.Shell`")",
    ("rc = shell.Run(`"" + ($cmdLine -replace '"', '""') + "`", 0, True)"),
    "WScript.Quit rc"
)
# UTF-16 with BOM ("Unicode"): WSH reads it, and it keeps non-ASCII folder names intact.
Set-Content -Path $launcher -Value $vbs -Encoding Unicode
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "//B //Nologo `"$launcher`"" -WorkingDirectory $Repo

# "powershell -File ... -At 09:00,18:00" passes one string "09:00,18:00": split it here.
$times = @($At | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($times.Count -eq 0) { throw "give at least one time with -At, e.g. -At 08:00,14:00,20:00" }
$formats = [string[]]@("HH:mm", "H:mm")
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$triggers = @()
foreach ($time in $times) {
    try {
        $at = [datetime]::ParseExact($time, $formats, $culture, [System.Globalization.DateTimeStyles]::None)
    } catch {
        throw "invalid time '$time' (use the 24-hour clock, e.g. 08:00 or 20:30)"
    }
    $triggers += New-ScheduledTaskTrigger -Daily -At $at
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes ([math]::Max(120, 15 * $Count)))

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($WhenLoggedOff) {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
} else {
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
}

Register-ScheduledTask -TaskName $TaskName `
    -Description "autoshorts: make $Count short video(s) per run ($uploadArgs). Log: $logFile" `
    -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Force | Out-Null

$when = $times -join ", "
Write-Host "Scheduled task '$TaskName' registered: daily at $when, running as $user." -ForegroundColor Green
Write-Host "  command: autoshorts batch -n $Count $uploadArgs (hidden window, via $launcher)"
if ($uploadArgs -eq "--no-upload") {
    Write-Host "  videos are only made, not uploaded; once your YouTube API project is audited, run this again with -Upload youtube"
}
Write-Host "  log:     $logFile"
Write-Host "  test now:  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  remove:    powershell -ExecutionPolicy Bypass -File deploy\windows\schedule.ps1 -Remove"
if (-not $WhenLoggedOff) {
    Write-Host "  (runs only while you are signed in; use -WhenLoggedOff from an admin PowerShell to change that)"
}
