param(
    [string]$PythonPath = "",
    [string]$DistPath = "release-v03-fixed"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $PythonPath) {
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $PythonPath = $venvPython
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $pythonCommand) {
            throw "Python was not found. Pass -PythonPath to a Python installation with tkinter and PyInstaller."
        }
        $PythonPath = $pythonCommand.Source
    }
}

$pythonDir = Split-Path -Parent $PythonPath
$pythonRoots = @(
    $pythonDir,
    (Split-Path -Parent $pythonDir)
)
$pythonRoot = $null
foreach ($candidate in $pythonRoots) {
    if (Test-Path -LiteralPath (Join-Path $candidate "tcl\tcl8.6\init.tcl")) {
        $pythonRoot = $candidate
        break
    }
}
if (-not $pythonRoot) {
    throw "Could not locate the Tcl/Tk data directory beside Python."
}
$tclData = Join-Path $pythonRoot "tcl\tcl8.6"
$tkData = Join-Path $pythonRoot "tcl\tk8.6"

if (-not (Test-Path -LiteralPath (Join-Path $tclData "init.tcl"))) {
    throw "Tcl data directory is incomplete: $tclData"
}
if (-not (Test-Path -LiteralPath (Join-Path $tkData "tk.tcl"))) {
    throw "Tk data directory is incomplete: $tkData"
}

# These variables must exist before PyInstaller starts its isolated Tcl probe.
$env:TCL_LIBRARY = $tclData
$env:TK_LIBRARY = $tkData

$spec = Join-Path $root "ChineseExamTypesetter_v03_fixed.spec"
$dist = Join-Path $root $DistPath
$work = Join-Path $root "work\build-v03-fixed-verified"

Push-Location $root
try {
    & $PythonPath -m PyInstaller --noconfirm --clean --distpath $dist --workpath $work $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
