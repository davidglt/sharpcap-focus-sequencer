@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "SEQUENCER=%SCRIPT_DIR%focus_sequencer.py"

"%PYTHON%" "%SEQUENCER%" --tube main %*
exit /b %ERRORLEVEL%
