$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$EnvPython = Join-Path $ProjectRoot '.venv-next\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $EnvPython)) {
    $CandidatePython = $null
    foreach ($Version in @('3.12', '3.11')) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $Found = & py "-$Version" -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $Found) { $CandidatePython = $Found.Trim(); break }
        }
    }
    if (-not $CandidatePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
        $Found = & python -c 'import sys; print(sys.executable if sys.version_info[:2] in [(3,11),(3,12)] else "")'
        if ($LASTEXITCODE -eq 0 -and $Found) { $CandidatePython = $Found.Trim() }
    }
    if (-not $CandidatePython) {
        Write-Host '[ERROR] Install Python 3.12 (64-bit), enable Add Python to PATH, then retry.'
        Write-Host 'https://www.python.org/downloads/windows/'
        exit 1
    }
    Write-Host '[1/3] Creating isolated Python environment...'
    & $CandidatePython -m venv (Join-Path $ProjectRoot '.venv-next')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Write-Host '[2/3] Installing open-source dependencies. First install may take several minutes...'
& $EnvPython -m pip install --disable-pip-version-check -r (Join-Path $ProjectRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '[3/3] Checking local dependencies (TeX Live is optional for original-layout mode)...'
& $EnvPython (Join-Path $ProjectRoot 'pdfzh_cli.py') doctor
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '[OK] Double-click start.cmd. Configure DeepSeek API, then choose a PDF.'
