@echo off
setlocal

cd /d "%~dp0"

set "WAIT_SECONDS=%MODIALOGUES_WAIT_SECONDS%"
if not defined WAIT_SECONDS set "WAIT_SECONDS=30"

:loop
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\process_matches.ps1 -Name python.exe -Pattern "scripts[\\/]+run_ollama\.py" >nul 2>nul
if errorlevel 1 call summarize_modules.bat %*
timeout /t %WAIT_SECONDS% /nobreak >nul
goto loop
