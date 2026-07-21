@echo off
setlocal

title Elaina Launcher

cd /d "%~dp0"

echo Starting Elaina desktop app...

start "Elaina Desktop" cmd /k ^
    "cd /d "%~dp0desktop" && npm start"

echo Waiting for Electron to open...
timeout /t 3 /nobreak >nul

echo Starting Elaina Python backend...

start "Elaina Python" cmd /k ^
    ".venv\Scripts\activate.bat && python main.py"

exit