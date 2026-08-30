@echo off
REM run_focus_guide.bat
REM Wrapper for focus_sequencer.py — Guide tube (50ED + ASI224MC)
REM
REM Connects directly to ASCOM.EAF_2.Focuser (no Device Hub needed:
REM SharpCap does not access the guide tube EAF).
REM Uses sharpcap_focus_state_guide.json as state file.
REM
REM All extra arguments are forwarded to the script, e.g.:
REM   run_focus_guide.bat --backlash 0 --min-correction 100 --dry-run

set "DIR=%~dp0"
cd /d "%DIR%"

set "PYTHON=%DIR%.venv\Scripts\python.exe"
set "SCRIPT=%DIR%focus_sequencer.py"
set "STATE_JSON=%DIR%..\sharpcap-focus-temperature\sharpcap_focus_state_guide.json"

"%PYTHON%" "%SCRIPT%" --ascom-id "ASCOM.EAF_2.Focuser" --state-json "%STATE_JSON%" %*
