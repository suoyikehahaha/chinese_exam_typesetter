param(
    [string]$Name = "ChineseExamTypesetter_0.3"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
}

$pythonRoot = Split-Path -Parent (Split-Path -Parent $pythonPath)
$runtimeDlls = Join-Path $pythonRoot "DLLs"
$runtimeTcl = Join-Path $pythonRoot "tcl"
$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", $Name,
    "--hidden-import", "desktop_app_v03",
    "--hidden-import", "desktop_app_current_v01",
    "--hidden-import", "app.editable_a4_canvas_v03",
    "--hidden-import", "app.score_summary_v03",
    "--hidden-import", "tkinter",
    "--hidden-import", "_tkinter",
    "--collect-all", "tkinter",
    "--add-data", "templates;templates",
    "--add-data", "samples;samples",
    "--add-data", "assets;assets"
)

$icon = Join-Path $root "assets\app-icon-v1.ico"
if (Test-Path -LiteralPath $icon) {
    $arguments += @("--icon", $icon)
}

$tkinterPyd = Join-Path $runtimeDlls "_tkinter.pyd"
$tclDll = Join-Path $runtimeDlls "tcl86t.dll"
$tkDll = Join-Path $runtimeDlls "tk86t.dll"
$tclData = Join-Path $runtimeTcl "tcl8.6"
$tkData = Join-Path $runtimeTcl "tk8.6"
if ((Test-Path -LiteralPath $tkinterPyd) -and (Test-Path -LiteralPath $tclDll) -and (Test-Path -LiteralPath $tkDll) -and (Test-Path -LiteralPath $tclData) -and (Test-Path -LiteralPath $tkData)) {
    $arguments += @(
        "--add-binary", "$tkinterPyd;."
        "--add-binary", "$tclDll;."
        "--add-binary", "$tkDll;."
        "--add-data", "$tclData;_tcl_data"
        "--add-data", "$tkData;_tk_data"
    )
}

$arguments += "windows_launcher_v03.py"
Push-Location $root
try {
    & $pythonPath @arguments
}
finally {
    Pop-Location
}
