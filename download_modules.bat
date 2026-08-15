@echo off
setlocal

cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\fetch_modules.py --download %*
) else (
  python scripts\fetch_modules.py --download %*
)

endlocal
