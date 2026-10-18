@echo off
rem Gets the latest Resell Toolkit version from GitHub. Your data, keys and settings are kept.
title Resell Toolkit - Update
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m resell update
echo.
pause
