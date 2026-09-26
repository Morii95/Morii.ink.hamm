@echo off
rem SCAD Studio starten (Windows) – Doppelklick genuegt.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 start.py %*
) else (
  where python >nul 2>nul
  if %errorlevel%==0 (
    python start.py %*
  ) else (
    echo Python wurde nicht gefunden. Bitte von https://www.python.org/downloads/ installieren
    echo und bei der Installation "Add python.exe to PATH" anhaken.
  )
)
pause
