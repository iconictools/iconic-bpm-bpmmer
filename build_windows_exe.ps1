param(
  [switch]$Clean
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
  throw "Python 3.9+ is required to build the Windows executable (3.11 recommended)."
}

$pythonCmd = Get-PythonCommand
$venvDir = Join-Path $ScriptDir ".venv-build-win"
$distDir = Join-Path $ScriptDir "dist"
$buildDir = Join-Path $ScriptDir "build"

if ($Clean) {
  Remove-Item -Recurse -Force $venvDir,$distDir,$buildDir -ErrorAction SilentlyContinue
}

if (-not (Test-Path $venvDir)) {
  & $pythonCmd.Exe @($pythonCmd.Args) -m venv $venvDir
}

$pipExe = Join-Path $venvDir "Scripts\pip.exe"

& $pipExe install --quiet --upgrade pip
& $pipExe install --quiet -r (Join-Path $ScriptDir "requirements.txt")
& $pipExe install --quiet pyinstaller

& (Join-Path $venvDir "Scripts\pyinstaller.exe") `
  --noconfirm `
  --clean `
  --name "Ultimate BPM Finder" `
  --onedir `
  --add-data "templates;templates" `
  --add-data "static;static" `
  --collect-all librosa `
  --collect-all numba `
  --collect-all scipy `
  --collect-all sklearn `
  --collect-all soundfile `
  app.py

Write-Host ""
Write-Host "Built executable:"
Write-Host "  $ScriptDir\dist\Ultimate BPM Finder\Ultimate BPM Finder.exe"
