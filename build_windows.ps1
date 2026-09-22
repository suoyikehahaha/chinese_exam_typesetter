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
# A virtual environment keeps Tcl/Tk in the base interpreter, not Scripts.
$pythonRoot = & $PythonPath -B -c "import sys, struct, tkinter; assert sys.version_info >= (3, 11), 'Python 3.11+ required'; assert struct.calcsize('P') == 8, '64-bit Python required'; print(sys.base_prefix)"
if ($LASTEXITCODE -ne 0) { throw "A 64-bit Python 3.11+ installation with Tkinter is required." }
if (-not (Test-Path -LiteralPath (Join-Path $pythonRoot "tcl\tcl8.6\init.tcl"))) {
    throw "Could not locate the Tcl/Tk data directory."
}
$previousTcl = $env:TCL_LIBRARY
$previousTk = $env:TK_LIBRARY
$previousBytecode = $env:PYTHONDONTWRITEBYTECODE
$releasePath = if ([IO.Path]::IsPathRooted($DistPath)) { $DistPath } else { Join-Path $root $DistPath }
Push-Location $root
try {
    & $PythonPath -B -c "import PyInstaller, docx, PIL, pypdf, pypdfium2"
    if ($LASTEXITCODE -ne 0) { throw "Install requirements-runtime.txt and requirements-build.txt with the selected Python before building." }
    $env:TCL_LIBRARY = Join-Path $pythonRoot "tcl\tcl8.6"
    $env:TK_LIBRARY = Join-Path $pythonRoot "tcl\tk8.6"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    & $PythonPath -B -m PyInstaller --noconfirm --clean `
        --distpath $releasePath `
        --workpath (Join-Path $root "work\build") `
        (Join-Path $root "ChineseExamTypesetter.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
}
finally {
    $env:TCL_LIBRARY = $previousTcl
    $env:TK_LIBRARY = $previousTk
    $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
    Pop-Location
}
