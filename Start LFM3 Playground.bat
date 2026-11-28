@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-lfm3-playground.ps1"
if errorlevel 1 pause
