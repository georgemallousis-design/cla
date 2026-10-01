<#
.SYNOPSIS
    Install autoshorts on Windows 10/11: Python, FFmpeg, a virtualenv and the package.

.DESCRIPTION
    Run from anywhere (PowerShell 5.1 or 7):

        powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1

    1. Installs Python 3.12 and FFmpeg with winget when they are missing.
    2. Creates <repo>\.venv and installs autoshorts with the YouTube upload and offline
       voice (pyttsx3) extras.
    3. Runs `autoshorts init` (config.yaml, .env, topics.txt; never overwrites) and
       `autoshorts doctor`.

.PARAMETER PythonVersion
    Python version to install with winget when no Python 3.10+ is found (default 3.12).

.PARAMETER NoOfflineTts
    Skip the pyttsx3 extra (offline Windows voices).
#>
[CmdletBinding()]
param(
    [string]$PythonVersion = "3.12",
    [switch]$NoOfflineTts
)

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Test-Path (Join-Path $Repo "pyproject.toml"))) {
    throw "$Repo does not look like the autoshorts repository (no pyproject.toml)."
}

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Update-SessionPath {
    # winget changes PATH for new terminals only; pick up the new value in this one.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Test-Winget {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "winget was not found. Install 'App Installer' from the Microsoft Store, or install Python 3.10+ and FFmpeg by hand (see README)."
    }
}

function Find-Python {
    # Returns @(exe, args...) for a Python >= 3.10, or $null. Skips the Microsoft Store stub.
    $candidates = @(
        @("py", "-$PythonVersion"),
        @("py", "-3"),
        @("python")
    )
    foreach ($cand in $candidates) {
        if (-not (Get-Command $cand[0] -ErrorAction SilentlyContinue)) { continue }
        $exeArgs = @()
        if ($cand.Count -gt 1) { $exeArgs = $cand[1..($cand.Count - 1)] }
        try {
            $out = & $cand[0] @exeArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch {
            continue
        }
        if ($LASTEXITCODE -ne 0 -or -not $out) { continue }
        $version = [version]("$out".Trim())
        if ($version -ge [version]"3.10") { return ,$cand }
    }
    return $null
}

# --------------------------------------------------------------------------- Python
Write-Step "Checking Python"
$python = Find-Python
if (-not $python) {
    Test-Winget
    Write-Host "Installing Python $PythonVersion with winget..."
    winget install -e --id "Python.Python.$PythonVersion" --scope user --accept-package-agreements --accept-source-agreements
    Update-SessionPath
    $python = Find-Python
    if (-not $python) {
        throw "Python was installed but is not on PATH yet. Open a NEW PowerShell window and run this script again."
    }
}
$pyExe = $python[0]
$pyArgs = @()
if ($python.Count -gt 1) { $pyArgs = $python[1..($python.Count - 1)] }
Write-Host ("Using: " + (& $pyExe @pyArgs -c "import sys; print(sys.executable, sys.version.split()[0])"))

# --------------------------------------------------------------------------- FFmpeg
Write-Step "Checking FFmpeg"
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Test-Winget
    Write-Host "Installing FFmpeg (Gyan.FFmpeg, includes libass and libx264) with winget..."
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    Update-SessionPath
}
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host ((& ffmpeg -hide_banner -version | Select-Object -First 1))
} else {
    Write-Warning "FFmpeg is installed but not on PATH in this window. Open a NEW PowerShell window before using autoshorts."
}

# --------------------------------------------------------------------------- virtualenv
Write-Step "Creating the virtualenv in $Repo\.venv"
$venvPython = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    & $pyExe @pyArgs -m venv (Join-Path $Repo ".venv")
    if ($LASTEXITCODE -ne 0) { throw "could not create the virtualenv" }
}
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }

$extras = "youtube,offline-tts"
if ($NoOfflineTts) { $extras = "youtube" }
& $venvPython -m pip install -e "${Repo}[${extras}]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --------------------------------------------------------------------------- config + check
$autoshorts = Join-Path $Repo ".venv\Scripts\autoshorts.exe"
Push-Location $Repo
try {
    Write-Step "Creating config.yaml, .env and topics.txt (existing files are kept)"
    & $autoshorts init
    Write-Step "Checking the setup (autoshorts doctor)"
    & $autoshorts doctor
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done. Next steps:" -ForegroundColor Green
Write-Host "  cd `"$Repo`""
Write-Host "  .\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"
Write-Host "  notepad .env                      # optional free API keys"
Write-Host "  autoshorts make                   # your first video, in .\output\"
Write-Host "  powershell -ExecutionPolicy Bypass -File deploy\windows\schedule.ps1   # 3 videos a day"
