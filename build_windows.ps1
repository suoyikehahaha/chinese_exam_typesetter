param(
    [string]$PythonPath = "",
    [string]$DistPath = "release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $PythonPath) {
    $preferred = "C:\Users\84175\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $preferred) { $PythonPath = $preferred }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) { throw "Python was not found." }
        $PythonPath = $pythonCommand.Source
    }
}
$pythonRoot = Split-Path -Parent $PythonPath
if (-not (Test-Path -LiteralPath (Join-Path $pythonRoot "tcl\tcl8.6\init.tcl"))) {
    throw "Could not locate the Tcl/Tk data directory."
}
$env:TCL_LIBRARY = Join-Path $pythonRoot "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $pythonRoot "tcl\tk8.6"
$pyInstallerCache = Join-Path $root "work\pyinstaller"
if (Test-Path -LiteralPath (Join-Path $pyInstallerCache "PyInstaller\__init__.py")) {
    if ($env:PYTHONPATH) { $env:PYTHONPATH = "$pyInstallerCache;$env:PYTHONPATH" }
    else { $env:PYTHONPATH = $pyInstallerCache }
}
Push-Location $root
try {
    & $PythonPath -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $root $DistPath) `
        --workpath (Join-Path $root "work\build") `
        (Join-Path $root "ChineseExamTypesetter.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
}
finally { Pop-Location }
