@echo off
setlocal

cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\parse_modules.py %*
) else (
  python scripts\parse_modules.py %*
)

endlocal
