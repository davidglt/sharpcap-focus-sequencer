@echo off
REM SharpCap Advanced Sequencer wrapper
REM Call this from RUN SCRIPT in the sequence to apply thermal focus correction.
REM Uses the virtual environment Python so pywin32/ASCOM dependencies are available.

"%~dp0.venv\Scripts\python.exe" "%~dp0focus_sequencer.py"
