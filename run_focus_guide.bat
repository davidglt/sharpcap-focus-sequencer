@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "SEQUENCER=%SCRIPT_DIR%focus_sequencer.py"
set "GUIDE_STATE=%SCRIPT_DIR%..\sharpcap-focus-temperature\sharpcap_focus_state_guide.json"

"%PYTHON%" "%SEQUENCER%" --tube guide --ascom-id ASCOM.EAF_2.Focuser --state-json "%GUIDE_STATE%" %*
exit /b %ERRORLEVEL%
