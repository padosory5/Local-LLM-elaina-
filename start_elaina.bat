@echo off
setlocal

rem Electron now starts and stops the Python backend itself.
cd /d "%~dp0desktop"
call npm start

endlocal
exit /b
