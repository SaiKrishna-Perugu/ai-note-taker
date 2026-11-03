<#
.SYNOPSIS
  Builds the Windows install package: dist\installer\AI-Note-Taker-<version>-win-x64.zip
  (extract, double-click Install.cmd). Add -SetupExe to also compile an Inno Setup .exe.

.DESCRIPTION
  The app is shipped as the official, Microsoft-/PSF-signed Python embeddable runtime plus the pinned
  PyPI wheels, not as a freshly packed .exe. Windows Smart App Control blocks new unsigned executables
  (PyInstaller output included) but allows the signed interpreter and widely used wheels.

  Steps: uv sync, unit tests (-SkipTests), wheel build, stage runtime + packages, prune, precompile,
  smoke-test the staged copy with the bundled interpreter, compile the Inno Setup installer.
  Requires: uv, Inno Setup 6 (winget install JRSoftware.InnoSetup), internet for the first build.
#>
param([switch]$SkipTests, [switch]$SetupExe)
$ErrorActionPreference = 'Stop'
$PythonVersion = '3.13.14'

# Native tools print progress to stderr. Windows PowerShell 5.1 turns that into a terminating error
# under 'Stop', so run them with 'Continue' and judge by exit code.
function Invoke-Native([string]$What, [scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Command 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}
function Step([string]$Text) { Write-Output "`n=== $Text" }

Push-Location $PSScriptRoot
try {
    $version = ([regex]::Match((Get-Content pyproject.toml -Raw), '(?m)^version\s*=\s*"([^"]+)"')).Groups[1].Value
    if (-not $version) { throw 'Could not read the version from pyproject.toml.' }

    if ($SetupExe) {
        $iscc = @(
            (Get-Command iscc -ErrorAction SilentlyContinue).Source,
            "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if (-not $iscc) { throw 'Inno Setup 6 not found. Install it: winget install JRSoftware.InnoSetup' }
    }

    Step 'Dependencies'
    Invoke-Native 'Dependency sync' { uv sync --group dev }

    if (-not $SkipTests) {
        Step 'Unit tests'
        $env:QT_QPA_PLATFORM = 'offscreen'
        try { Invoke-Native 'Unit tests (not building an installer)' { uv run python -m unittest discover -s tests } }
        finally { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
    }

    $build = Join-Path $PSScriptRoot 'build'
    $package = Join-Path $build 'package'
    $stage = Join-Path $package 'app'
    $cache = Join-Path $build 'cache'
    Remove-Item $package, "$build\wheels" -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory $stage, $cache -Force | Out-Null

    Step 'Application wheel'
    Invoke-Native 'Wheel build' { uv build --wheel --out-dir "$build\wheels" }
    $wheel = (Get-ChildItem "$build\wheels\*.whl" | Select-Object -First 1).FullName

    Step 'Pinned runtime requirements'
    $requirements = "$build\requirements.txt"
    Invoke-Native 'Requirements export' { uv export --frozen --no-dev --no-hashes --no-emit-project --output-file $requirements }
    # Qt Essentials has everything the app imports; the full PySide6 meta package adds ~hundreds of MB.
    # sarvamai/pycaw/comtypes are not used by the app (only by the root prototype / hardware checks).
    Get-Content $requirements |
        Where-Object { $_.Trim() -and $_ -notmatch '^\s*#' -and $_ -notmatch '^(pyside6|pyside6-addons|sarvamai|pycaw|comtypes)==' } |
        Set-Content "$build\requirements.runtime.txt" -Encoding ascii

    Step "Python $PythonVersion embeddable runtime"
    $zip = "$cache\python-$PythonVersion-embed-amd64.zip"
    if (-not (Test-Path $zip)) {
        Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $zip -UseBasicParsing
    }
    Expand-Archive $zip "$stage\runtime" -Force
    foreach ($name in 'python.exe', 'pythonw.exe') {
        $signature = Get-AuthenticodeSignature "$stage\runtime\$name"
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
            throw "$name from the downloaded runtime is not validly signed by the Python Software Foundation ($($signature.Status))."
        }
    }
    $tag = ($PythonVersion -split '\.')[0..1] -join ''
    # Isolated mode: look only in the zip, this folder and the app's site-packages.
    "python$tag.zip`r`n.`r`n..\site-packages`r`n" | Set-Content "$stage\runtime\python$tag._pth" -Encoding ascii -NoNewline
    # App-local Microsoft C++ runtime so Qt starts on PCs without the Visual C++ Redistributable.
    foreach ($dll in 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll', 'concrt140.dll', 'vcruntime140_threads.dll') {
        $source = Join-Path $env:SystemRoot "System32\$dll"
        if (Test-Path $source) { Copy-Item $source "$stage\runtime\" -Force }
    }

    Step 'Install packages'
    $packages = "$stage\site-packages"
    Invoke-Native 'Dependency install' {
        uv pip install --target $packages --python-version 3.13 --python-platform windows --only-binary ':all:' --no-deps -r "$build\requirements.runtime.txt"
    }
    Invoke-Native 'Application install' {
        uv pip install --target $packages --python-version 3.13 --python-platform windows --no-deps $wheel
    }

    Step 'Prune'
    $qt = "$packages\PySide6"
    foreach ($folder in 'translations', 'qml', 'metatypes', 'typesystems', 'include', 'glue', 'scripts', 'examples') {
        Remove-Item "$qt\$folder" -Recurse -Force -ErrorAction SilentlyContinue
    }
    # No third-party executables in the bundle (Qt developer tools etc.).
    Get-ChildItem $packages -Recurse -Filter *.exe -File -ErrorAction SilentlyContinue | Remove-Item -Force
    foreach ($pattern in 'Qt6Quick*', 'Qt6Qml*', 'Qt6Pdf*', 'Qt6Designer*', 'Qt6Help*', 'Qt63D*', 'Qt6Sql*', 'Qt6Test*', 'Qt6VirtualKeyboard*', 'Qt6WebEngine*') {
        Get-ChildItem $qt -Recurse -Filter "$pattern.dll" -File -ErrorAction SilentlyContinue | Remove-Item -Force
    }
    Get-ChildItem $packages -Recurse -Directory -Filter tests -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch 'PySide6' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    Step 'Precompile'
    & "$stage\runtime\python.exe" -m compileall -q $packages | Out-Null   # a few vendored files are intentionally invalid

    Step 'Smoke test with the bundled interpreter'
    Copy-Item 'packaging\app.ico' "$stage\app.ico"
    $env:QT_QPA_PLATFORM = 'offscreen'
    try { Invoke-Native 'Runtime smoke test' { & "$stage\runtime\python.exe" 'packaging\smoke_runtime.py' } }
    finally { Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue }
    $megabytes = [int]((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
    Write-Output "Staged app: $megabytes MB"

    Step 'Install package (zip)'
    Set-Content "$stage\VERSION" $version -Encoding ascii
    Copy-Item 'packaging\Install.cmd', 'packaging\Install-AINoteTaker.ps1' $package
    Copy-Item 'packaging\README-install.txt' "$package\README.txt"
    $outDir = Join-Path $PSScriptRoot 'dist\installer'
    New-Item -ItemType Directory $outDir -Force | Out-Null
    $zipPath = Join-Path $outDir "AI-Note-Taker-$version-win-x64.zip"
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($package, $zipPath, [IO.Compression.CompressionLevel]::Optimal, $false)
    $hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash
    '{0}  {1}' -f $hash, (Split-Path $zipPath -Leaf) | Set-Content "$zipPath.sha256"
    Write-Output "Package:   $zipPath ($([int]((Get-Item $zipPath).Length / 1MB)) MB)"
    Write-Output "SHA256:    $hash"

    if ($SetupExe) {
        Step 'Optional Setup.exe (Inno Setup; unsigned, Smart App Control may block it)'
        Invoke-Native 'Inno Setup compile' { & $iscc "/DAppVersion=$version" 'packaging\AINoteTaker.iss' }
        Write-Output "Setup.exe: $outDir\AI-Note-Taker-Setup-$version.exe"
    }
} finally {
    Pop-Location
}
