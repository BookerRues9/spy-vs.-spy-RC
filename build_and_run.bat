@echo off
rem Builds the port (scripts\build.ps1) and starts the game (scripts\run.ps1).
rem Run scripts\regenerate.ps1 once before the first build (see README).
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1"
if errorlevel 1 (
    echo Build failed. See C:\svs\build.log
    pause
    exit /b 1
)
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1"
