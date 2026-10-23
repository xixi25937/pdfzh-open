@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "PDFZH_RC=%errorlevel%"
if not "%PDFZH_RC%"=="0" pause
exit /b %PDFZH_RC%
