@echo off
rem Double-click to install (or reinstall) the Resell Toolkit. Downloads the installer from GitHub and runs it.
title Resell Toolkit installer
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/dom1911k/flipping_scout/claude/gracious-dirac-39brnb/windows/install.ps1 | iex"
pause
