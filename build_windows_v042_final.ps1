param(
    [string]$PythonPath = "",
    [string]$DistPath = "release-v042-final"
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
            throw "Python was not found. Pass -PythonPath explicitly."
        }
        $PythonPath = $pythonCommand.Source
    }
}

$pythonDir = Split-Path -Parent $PythonPath
$pythonRoots = @($pythonDir, (Split-Path -Parent $pythonDir))
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

$env:TCL_LIBRARY = Join-Path $pythonRoot "tcl\tcl8.6"
$env:TK_LIBRARY = Join-Path $pythonRoot "tcl\tk8.6"
$spec = Join-Path $root "ChineseExamTypesetter_v042_final.spec"
$dist = Join-Path $root $DistPath
$work = Join-Path $root "work\build-v042-final"

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

