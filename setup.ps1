# pdfzh local setup: BabelDOC layout + DeepSeek translation.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$Root = $PSScriptRoot
$Venv = Join-Path $Root '.venv-next'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$Requirements = Join-Path $Root 'requirements-next.txt'
$LocalHome = Join-Path $Root '.home-next'

$Python = $null
foreach ($Candidate in @('D:\Python312\python.exe', 'D:\Python311\python.exe')) {
    if (Test-Path -LiteralPath $Candidate) {
        $Python = $Candidate
        break
    }
}
if (-not $Python) {
    Write-Host '[ERROR] Python 3.11 or 3.12 was not found.' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host '[1/3] Creating the project environment...'
    & $Python -m venv $Venv
}

Write-Host '[2/3] Installing BabelDOC 0.6.4...'
& $VenvPython -m pip install --disable-pip-version-check --progress-bar off -r $Requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:HOME = $LocalHome
$env:USERPROFILE = $LocalHome
$env:HF_HOME = Join-Path $LocalHome 'huggingface'
$env:TEMP = Join-Path $LocalHome 'tmp'
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = $Root
New-Item -ItemType Directory -Force -Path $env:HF_HOME, $env:TEMP | Out-Null

Write-Host '[3/3] Downloading and checking the minimal free local assets...'
& $VenvPython (Join-Path $Root 'pdfzh_next.py') --warmup
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '[OK] Installation complete.' -ForegroundColor Green
Write-Host 'Run: .\pdfzh-run.cmd your-paper.pdf'
