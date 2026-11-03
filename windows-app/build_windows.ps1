$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    uv sync --group dev
    if ($LASTEXITCODE -ne 0) { throw 'Dependency sync failed.' }
    uv run pyinstaller --noconfirm 'AI Note Taker.spec'
    if ($LASTEXITCODE -ne 0) { throw 'Windows application build failed.' }
    Write-Output "Built: $PSScriptRoot/dist/AI Note Taker/AI Note Taker.exe"
    Write-Output 'Keep the entire AI Note Taker folder together when sharing the app.'
} finally {
    Pop-Location
}
