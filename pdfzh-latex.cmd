@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"%~dp0.venv-next\Scripts\python.exe" "%~dp0pdfzh_latex.py" %*
exit /b %errorlevel%
