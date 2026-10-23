@echo off
REM ============================================================
REM  pdfzh.cmd - PDF 中英对照翻译工具一键启动器
REM
REM  用法：
REM      pdfzh.cmd 论文.pdf
REM      pdfzh.cmd 论文.pdf --pages 1-5
REM      pdfzh.cmd --help
REM
REM  默认使用 BabelDOC 0.6.4 本地排版 + DeepSeek 翻译。
REM  版面分析、字体、拼版和质检均在本机运行。
REM ============================================================
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
