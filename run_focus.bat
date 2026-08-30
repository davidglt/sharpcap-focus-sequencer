@echo off
REM run_focus.bat
REM SharpCap Advanced Sequencer wrapper for focus_sequencer.py
REM All arguments are forwarded to the script, e.g.:
REM   run_focus.bat --dry-run

set "DIR=%~dp0"
cd /d "%DIR%"

set "PYTHON=%DIR%.venv\Scripts\python.exe"
set "SCRIPT=%DIR%focus_sequencer.py"

"%PYTHON%" "%SCRIPT%" --backlash 500 --min-correction 50 %*
