@echo off
REM run_focus.bat
REM SharpCap Advanced Sequencer wrapper for focus_sequencer.py
REM All arguments are forwarded to the script, e.g.:
REM   run_focus.bat --backlash 500 --min-correction 20 --dry-run

set DIR=C:\astro\sharpcap-focus-sequencer
set PYTHON=%DIR%\.venv\Scripts\python.exe
set SCRIPT=%DIR%\focus_sequencer.py

cd /d %DIR%
"%PYTHON%" "%SCRIPT%" %*
