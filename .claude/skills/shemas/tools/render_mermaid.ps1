param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,

    [string]$OutputFile,

    [int]$Width = 680,

    [double]$Scale = 1.0
)

$RepoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$ScriptPath = Join-Path $PSScriptRoot "render_mermaid.py"

if (-not (Test-Path $PythonExe)) {
    throw "Не найден проектный интерпретатор Python: $PythonExe"
}

$CmdArgs = @(
    $ScriptPath,
    "--input", $InputFile,
    "--width", $Width,
    "--scale", $Scale
)

if ($OutputFile) {
    $CmdArgs += @("--output", $OutputFile)
}

& $PythonExe @CmdArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
