param(
  [ValidateRange(1,65535)]
  [int]$Port = 5000,
  [switch]$Install
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Get-PythonCommand {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    return @{ Exe = 'py'; Args = @('-3') }
  }
  if (Get-Command python -ErrorAction SilentlyContinue) {
    return @{ Exe = 'python'; Args = @() }
  }
  return $null
}

function Test-PythonVersion {
  param([hashtable]$Command)
  $version = & $Command.Exe @($Command.Args) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
  if ($LASTEXITCODE -ne 0) { return $false }
  $parts = $version.Trim().Split('.')
  if ($parts.Length -lt 2) { return $false }
  return ([int]$parts[0] -gt 3) -or (([int]$parts[0] -eq 3) -and ([int]$parts[1] -ge 9))
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  iconic-bpm-bpmmer — Ultimate BPM Finder (Windows)"
Write-Host "============================================================"
Write-Host ""

$pythonCmd = Get-PythonCommand
if (-not $pythonCmd -or -not (Test-PythonVersion -Command $pythonCmd)) {
  Write-Error "Python 3.9+ is required. Install from https://www.python.org/downloads/windows/ and run start.bat again."
}

$venvDir = Join-Path $ScriptDir ".venv"
if ($Install -and (Test-Path $venvDir)) {
  Write-Host "Removing existing virtual environment..."
  Remove-Item -Recurse -Force $venvDir
}

if (-not (Test-Path $venvDir)) {
  Write-Host "Creating virtual environment..."
  & $pythonCmd.Exe @($pythonCmd.Args) -m venv $venvDir
}

$pyExe = Join-Path $venvDir "Scripts\python.exe"
$pipExe = Join-Path $venvDir "Scripts\pip.exe"
$reqFile = Join-Path $ScriptDir "requirements.txt"
$stamp = Join-Path $venvDir ".deps_installed"

$needsInstall = $Install -or -not (Test-Path $stamp) -or ((Get-Item $reqFile).LastWriteTimeUtc -gt (Get-Item $stamp).LastWriteTimeUtc)
if ($needsInstall) {
  Write-Host "Installing Python dependencies..."
  & $pipExe install --quiet --upgrade pip
  & $pipExe install --quiet -r $reqFile
  New-Item -Path $stamp -ItemType File -Force | Out-Null
  Write-Host "Dependencies installed."
} else {
  Write-Host "Dependencies are up-to-date (use -Install to force reinstall)."
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Warning "ffmpeg not found. MP3 export will be unavailable. Install with: winget install --id Gyan.FFmpeg -e"
}

New-Item -ItemType Directory -Force -Path (Join-Path $ScriptDir "uploads") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ScriptDir "outputs") | Out-Null

$appUrl = "http://localhost:$Port"
Write-Host ""
Write-Host "Starting server at $appUrl"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

Start-Process $appUrl
& $pyExe (Join-Path $ScriptDir "app.py") --port $Port
