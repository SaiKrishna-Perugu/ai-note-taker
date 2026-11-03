<#
.SYNOPSIS
  Installs (or uninstalls) AI Note Taker for the current user. No administrator rights needed.

.DESCRIPTION
  Copies the bundled app to %LOCALAPPDATA%\Programs\AI Note Taker, creates Start Menu and Desktop
  shortcuts, and registers the app under Settings > Apps so it can be uninstalled normally.
  Your recordings (Documents\AI Note Taker) and settings (%APPDATA%\AI Note Taker) are never touched.

  Usage:  Install.cmd   (double-click)
          powershell -ExecutionPolicy Bypass -File Install-AINoteTaker.ps1 [-NoDesktopShortcut] [-NoLaunch]
          ... -Uninstall
#>
param(
    [switch]$Uninstall,
    [switch]$NoDesktopShortcut,
    [switch]$NoLaunch,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\AI Note Taker')
)
$ErrorActionPreference = 'Stop'
$AppName = 'AI Note Taker'
$RegistryKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\AINoteTaker'
$StartMenuLink = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$AppName.lnk"
$DesktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk"

function Stop-RunningApp {
    # The GUI and its background recorder both run from the install folder.
    $running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like "$InstallDir\*" }
    if (-not $running) { return }
    Write-Output 'Closing AI Note Taker...'
    $running | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { $null = $_.CloseMainWindow() }
    Start-Sleep 3
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like "$InstallDir\*" } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep 1
}

function New-Shortcut([string]$Path) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = Join-Path $InstallDir 'runtime\pythonw.exe'
    $link.Arguments = '-m ai_note_taker.desktop'
    $link.WorkingDirectory = $InstallDir
    $link.IconLocation = Join-Path $InstallDir 'app.ico'
    $link.Description = 'Record meetings and get AI transcripts and notes'
    $link.Save()
}

if ($Uninstall) {
    Write-Output "Uninstalling $AppName from $InstallDir ..."
    Stop-RunningApp
    Remove-Item $StartMenuLink, $DesktopLink -Force -ErrorAction SilentlyContinue
    Remove-Item $RegistryKey -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $InstallDir) {
        # Remove everything except this running script, then delete the folder once we have exited.
        Get-ChildItem $InstallDir -Force | Where-Object { $_.Name -ne 'Setup' } | Remove-Item -Recurse -Force
        $command = "ping 127.0.0.1 -n 3 >nul & rmdir /s /q `"$InstallDir`""
        Start-Process cmd.exe -ArgumentList '/c', $command -WindowStyle Hidden
    }
    Write-Output "$AppName was removed. Your recordings and settings were kept."
    return
}

$source = Join-Path $PSScriptRoot 'app'
if (-not (Test-Path (Join-Path $source 'runtime\pythonw.exe'))) {
    throw "Cannot find the app files next to this script (expected: $source). Extract the whole zip first."
}
if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'Windows 10 or later is required.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'A 64-bit version of Windows is required.' }
$version = (Get-Content (Join-Path $source 'VERSION') -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $version) { $version = '0.0.0' }

Write-Output "Installing $AppName $version to $InstallDir ..."
Stop-RunningApp
if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
New-Item -ItemType Directory $InstallDir -Force | Out-Null
$null = robocopy $source $InstallDir /E /NFL /NDL /NJH /NJS /NP /R:2 /W:1   # exit codes below 8 mean success
if ($LASTEXITCODE -ge 8) { throw "Copying the app files failed (robocopy exit code $LASTEXITCODE)." }
# Files extracted from a downloaded zip carry a "downloaded from the internet" mark; remove it.
# Only needed (and only slow) when this package itself was marked.
if (Get-Content -LiteralPath $PSCommandPath -Stream Zone.Identifier -ErrorAction SilentlyContinue) {
    Get-ChildItem $InstallDir -Recurse -File -Force | Unblock-File -ErrorAction SilentlyContinue
}

# Keep this script so Settings > Apps can run the uninstall.
$setup = Join-Path $InstallDir 'Setup'
New-Item -ItemType Directory $setup -Force | Out-Null
Copy-Item $PSCommandPath (Join-Path $setup 'Install-AINoteTaker.ps1') -Force

New-Shortcut $StartMenuLink
if (-not $NoDesktopShortcut) { New-Shortcut $DesktopLink }

$uninstallCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$setup\Install-AINoteTaker.ps1`" -Uninstall -InstallDir `"$InstallDir`""
New-Item $RegistryKey -Force | Out-Null
$entries = @{
    DisplayName          = $AppName
    DisplayVersion       = $version
    Publisher            = 'SaiKrishna Perugu'
    InstallLocation      = $InstallDir
    DisplayIcon          = Join-Path $InstallDir 'app.ico'
    UninstallString      = $uninstallCommand
    QuietUninstallString = $uninstallCommand
    URLInfoAbout         = 'https://github.com/SaiKrishna-Perugu/ai-note-taker'
}
foreach ($name in $entries.Keys) { Set-ItemProperty $RegistryKey -Name $name -Value $entries[$name] }
Set-ItemProperty $RegistryKey -Name NoModify -Value 1 -Type DWord
Set-ItemProperty $RegistryKey -Name NoRepair -Value 1 -Type DWord
$kilobytes = [int]((Get-ChildItem $InstallDir -Recurse -File | Measure-Object Length -Sum).Sum / 1KB)
Set-ItemProperty $RegistryKey -Name EstimatedSize -Value $kilobytes -Type DWord

Write-Output "Installed. Find '$AppName' in the Start menu$(if (-not $NoDesktopShortcut) { ' or on your desktop' })."
if (-not $NoLaunch) {
    Start-Process (Join-Path $InstallDir 'runtime\pythonw.exe') -ArgumentList '-m', 'ai_note_taker.desktop' -WorkingDirectory $InstallDir
}
