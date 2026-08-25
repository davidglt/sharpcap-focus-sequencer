@echo off
REM SharpCap Advanced Sequencer wrapper
REM Call this from RUN SCRIPT in the sequence immediately after each SharpCap
REM autofocus to update focus_ref and T_ref in sharpcap_focus_state.json.
REM Uses the virtual environment Python of the sibling sharpcap-focus-temperature
REM repository so all dependencies are available without polluting this repo.
REM All arguments are forwarded to sharpcap_focuser.py, e.g.:
REM   run_update_reference.bat --ascom-id "ASCOM.EAF_2.Focuser"

"%~dp0..\sharpcap-focus-temperature\.venv\Scripts\python.exe" "%~dp0..\sharpcap-focus-temperature\sharpcap_focuser.py" %*
