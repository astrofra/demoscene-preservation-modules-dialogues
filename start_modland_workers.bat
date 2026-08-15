@echo off
setlocal

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\process_matches.ps1 -Name cmd.exe -Pattern "download_modules_loop\.bat --source modland-protracker" >nul 2>nul
if errorlevel 1 (
  start "" /min cmd /c call download_modules_loop.bat --source modland-protracker
  echo started download loop
) else (
  echo download loop already running
)

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\process_matches.ps1 -Name cmd.exe -Pattern "parse_modules_loop\.bat --source modland-protracker" >nul 2>nul
if errorlevel 1 (
  start "" /min cmd /c call parse_modules_loop.bat --source modland-protracker
  echo started parse loop
) else (
  echo parse loop already running
)

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\process_matches.ps1 -Name cmd.exe -Pattern "summarize_modules_loop\.bat --source modland-protracker" >nul 2>nul
if errorlevel 1 (
  start "" /min cmd /c call summarize_modules_loop.bat --source modland-protracker
  echo started summary loop
) else (
  echo summary loop already running
)

echo.
echo Active logs:
echo   data\logs\fetch_modules.log
echo   data\logs\run_ollama.log

endlocal
