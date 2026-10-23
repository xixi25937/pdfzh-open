@echo off
setlocal
if not exist "%~dp0.venv-next\Scripts\python.exe" (
    call "%~dp0install.cmd"
    if errorlevel 1 exit /b 1
)
call "%~dp0pdfzh.cmd" %*
set "PDFZH_RC=%errorlevel%"
pause
exit /b %PDFZH_RC%
