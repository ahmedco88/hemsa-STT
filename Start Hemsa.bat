@echo off
rem Double-click to launch Hemsa. No console window (pythonw). Safe to run again -
rem the app refuses a second instance and just tells you it's already running.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -m hemsa
