@echo off
setlocal

cd /d "%~dp0"

if not exist "run.sh" (
  echo [ERROR] run.sh not found in this folder.
  pause
  exit /b 1
)

where bash >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Bash is required. Install Git Bash and run this file again.
  echo Download: https://git-scm.com/download/win
  pause
  exit /b 1
)

echo Starting Ultimate BPM Finder...
bash "%~dp0run.sh"

if errorlevel 1 (
  echo.
  echo App exited with an error.
  pause
)
