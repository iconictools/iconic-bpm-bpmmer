@echo off
setlocal

cd /d "%~dp0"
set "APP_NAME=Ultimate BPM Finder"
set "SCRIPT=%~dp0run_windows.ps1"

if not exist "%SCRIPT%" (
  echo [ERROR] run_windows.ps1 not found in this folder.
  pause
  exit /b 1
)

echo Starting %APP_NAME%...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"

if errorlevel 1 (
  echo.
  echo App exited with an error.
  pause
)
