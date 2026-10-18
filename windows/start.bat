@echo off
rem Starts the Resell Toolkit web page and opens it in your browser. Keep this window open while you use it.
title Resell Toolkit - keep this window open
cd /d "%~dp0.."
set RESELL_SUPERVISED=1

rem Already running? Then just open the browser.
curl -s -o nul http://127.0.0.1:8765/research && (start "" http://127.0.0.1:8765/research & exit /b 0)

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 4; Start-Process 'http://127.0.0.1:8765/research'"
:loop
".venv\Scripts\python.exe" -m resell serve
rem Exit code 3 = the Update page installed a new version: restart on the new code.
if %errorlevel%==3 (echo Restarting with the new version... & goto loop)
echo.
echo The toolkit stopped. If you see an error above, copy it and send it to Claude.
pause
