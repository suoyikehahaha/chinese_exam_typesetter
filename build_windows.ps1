param(
    [string]$PythonPath = "",
    [string]$DistPath = "release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $PythonPath) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) { throw "Python was not found. Pass -PythonPath with a Python 3.11+ executable." }
    $PythonPath = $pythonCommand.Source
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
& $PythonPath -B -c "import sys, struct; assert sys.version_info >= (3, 11), 'Python 3.11+ required'; assert struct.calcsize('P') == 8, '64-bit Python required'"
if ($LASTEXITCODE -ne 0) { throw "A 64-bit Python 3.11+ installation is required." }

$previousBytecode = $env:PYTHONDONTWRITEBYTECODE
$releasePath = if ([IO.Path]::IsPathRooted($DistPath)) { $DistPath } else { Join-Path $root $DistPath }
Push-Location $root
try {
    & $PythonPath -B -c "import PyInstaller, PySide6.QtWidgets, docx, PIL, pypdf, pypdfium2"
    if ($LASTEXITCODE -ne 0) { throw "Install requirements-runtime.txt and requirements-build.txt with the selected Python before building." }
    $env:PYTHONDONTWRITEBYTECODE = "1"
    & $PythonPath -B -m PyInstaller --noconfirm --clean `
        --distpath $releasePath `
        --workpath (Join-Path $root "work\build") `
        (Join-Path $root "ChineseExamTypesetter.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
}
finally {
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
    Pop-Location
}
