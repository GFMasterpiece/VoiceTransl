@echo off
setlocal EnableExtensions

set "VOICETRANSL_ROOT=%~dp0"
if not exist "%VOICETRANSL_ROOT%backend\app\main.py" (
    echo VoiceTransl backend was not found: "%VOICETRANSL_ROOT%backend"
    exit /b 1
)
if not exist "%VOICETRANSL_ROOT%frontend\package.json" (
    echo VoiceTransl frontend was not found: "%VOICETRANSL_ROOT%frontend"
    exit /b 1
)

if not defined VOICETRANSL_CONDA_ENV set "VOICETRANSL_CONDA_ENV=asmr"
set "CONDA_COMMAND="
if defined VOICETRANSL_CONDA_COMMAND set "CONDA_COMMAND=%VOICETRANSL_CONDA_COMMAND%"

if not defined CONDA_COMMAND (
    where conda >nul 2>nul
    if not errorlevel 1 set "CONDA_COMMAND=conda"
)
if not defined CONDA_COMMAND if defined CONDA_EXE if exist "%CONDA_EXE%" set "CONDA_COMMAND=%CONDA_EXE%"
if not defined CONDA_COMMAND if exist "%USERPROFILE%\miniforge3\condabin\conda.bat" set "CONDA_COMMAND=%USERPROFILE%\miniforge3\condabin\conda.bat"
if not defined CONDA_COMMAND if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_COMMAND=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not defined CONDA_COMMAND if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_COMMAND=%USERPROFILE%\anaconda3\condabin\conda.bat"
if not defined CONDA_COMMAND (
    echo Could not find Conda. Install Miniforge/Miniconda or set VOICETRANSL_CONDA_COMMAND.
    exit /b 1
)

where npm.cmd >nul 2>nul
if errorlevel 1 (
    echo Could not find npm.cmd. Install Node.js and add it to PATH.
    exit /b 1
)

set "VOICETRANSL_CONDA_COMMAND=%CONDA_COMMAND%"
start "VoiceTransl Backend" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -Command "$ErrorActionPreference = 'Stop'; Set-Location -LiteralPath (Join-Path $env:VOICETRANSL_ROOT 'backend'); & $env:VOICETRANSL_CONDA_COMMAND run --no-capture-output -n $env:VOICETRANSL_CONDA_ENV python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1"
start "VoiceTransl Frontend" powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -NoExit -Command "$ErrorActionPreference = 'Stop'; Set-Location -LiteralPath (Join-Path $env:VOICETRANSL_ROOT 'frontend'); & npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort"

powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2"
start "" "http://127.0.0.1:5173/"
exit /b 0
