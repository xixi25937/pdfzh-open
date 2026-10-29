@echo off
REM pdfzh.cmd - PDF English-to-Chinese launcher. Usage: pdfzh.cmd paper.pdf / doctor / configure / results
REM Keep every line above "chcp 65001" ASCII: cmd parses them in the OEM code page (936),
REM and UTF-8 Chinese there can swallow line breaks and run comment text as a command.
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

set "ROOT=%~dp0"
set "PY=%ROOT%.venv-next\Scripts\python.exe"

if not exist "%PY%" (
    echo [错误] 未找到新版 Python 环境: %PY%
    echo        请先运行 setup.ps1 完成安装。
    exit /b 1
)

"%PY%" -u "%ROOT%pdfzh_cli.py" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
