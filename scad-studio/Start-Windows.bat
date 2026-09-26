@echo off
rem SCAD Studio starten (Windows) - Doppelklick genuegt.
cd /d "%~dp0"

rem Zuerst den Python-Starter "py" versuchen, sonst "python".
rem (Absichtlich mit goto statt Klammerbloecken: %errorlevel% wird in
rem  Klammerbloecken beim Einlesen ausgewertet und waere dann falsch.)
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 start.py %*
goto done

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python start.py %*
goto done

:no_python
echo Python wurde nicht gefunden.
echo Bitte von https://www.python.org/downloads/ installieren und bei der
echo Installation "Add python.exe to PATH" anhaken.

:done
pause
