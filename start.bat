@echo off
setlocal

cd /d "%~dp0"
set "APP_NAME=Ultimate BPM Finder"
set "EXE=%~dp0Ultimate BPM Finder.exe"
set "SCRIPT=%~dp0run_windows.ps1"

if not exist "%EXE%" (
  set "EXE=%~dp0dist\Ultimate BPM Finder\Ultimate BPM Finder.exe"
)

if exist "%EXE%" (
  echo Starting %APP_NAME%...
  start "" "%EXE%"
  exit /b 0
)

if not exist "%SCRIPT%" (
  echo [ERROR] No bundled executable or developer bootstrap script found.
  echo         Expected: "%~dp0Ultimate BPM Finder.exe"
  echo      or "%~dp0dist\Ultimate BPM Finder\Ultimate BPM Finder.exe"
  echo      and "%~dp0run_windows.ps1"
  echo.
  echo Build it with:
  echo   powershell -NoProfile -ExecutionPolicy Bypass -File build_windows_exe.ps1
  pause
  exit /b 1
)

echo [INFO] Bundled executable not found; running developer bootstrap script...
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"

if errorlevel 1 (
  echo.
  echo App exited with an error.
  pause
)
