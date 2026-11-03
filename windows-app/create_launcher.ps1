# Create a shortcut in this project; no system security settings are changed.
$ErrorActionPreference = 'Stop'
$pythonWindowed = Join-Path $PSScriptRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowed)) {
    throw 'Run uv sync in windows-app first.'
}
$shortcutPath = Join-Path $PSScriptRoot 'AI Note Taker.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonWindowed
$shortcut.Arguments = '"' + (Join-Path $PSScriptRoot 'Launch Note Taker.pyw') + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.Description = 'AI Note Taker — record, pause, resume, and transcribe meetings'
$shortcut.Save()
Write-Output $shortcutPath
