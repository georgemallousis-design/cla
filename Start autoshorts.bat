@echo off
rem Double-click to open the autoshorts dashboard in your browser.
cd /d "%~dp0"
if not exist ".venv\Scripts\autoshorts.exe" (
  echo Run deploy\windows\install.ps1 first.
  pause
  exit /b 1
)
".venv\Scripts\autoshorts.exe" ui
pause
