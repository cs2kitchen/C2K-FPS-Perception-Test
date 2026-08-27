param(
    [switch]$SkipInstall,
    [switch]$SkipInstaller,
    [switch]$RequireInstaller
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
$appName = "C2K FPS Perception Test"
$distRoot = Join-Path $repoRoot "dist"
$standaloneExe = Join-Path $distRoot "$appName.exe"
$portableZip = Join-Path $distRoot "$appName Portable.zip"
$installer = Join-Path $distRoot "$appName Setup.exe"
$legacyStandalone = Join-Path $distRoot $appName
$pyinstallerWork = Join-Path $PSScriptRoot "pyinstaller"
$specFile = Join-Path $PSScriptRoot "$appName.spec"
$requirements = Join-Path $repoRoot "requirements-build.txt"
$venvRoot = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

function Find-SystemPython {
    foreach ($commandName in @("python", "python3")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }
    return $null
}

function Find-InnoCompiler {
    $candidates = @(
        $env:ISCC_PATH,
        (Join-Path $repoRoot ".tools\Inno Setup 6\ISCC.exe"),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $pathCommand = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($pathCommand) {
        $candidates += $pathCommand.Source
    }
    return $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}

$systemPython = Find-SystemPython
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
}
elseif ($SkipInstall) {
    if (-not $systemPython) {
        throw "Python 3.12 or newer was not found."
    }
    $python = $systemPython
}
else {
    if (-not $systemPython) {
        throw "Python 3.12 or newer was not found."
    }
    Write-Host "Creating the local build environment..."
    & $systemPython -m venv $venvRoot
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        throw "Could not create the local Python build environment."
    }
    $python = $venvPython
}

if (-not $SkipInstall) {
    Write-Host "Installing build dependencies..."
    & $python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the build dependencies from requirements-build.txt."
    }
}

& $python -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is unavailable. Run build_release.bat without -SkipInstall or install requirements-build.txt."
}

foreach ($requiredFile in @(
    $specFile,
    (Join-Path $repoRoot "app\main.py"),
    (Join-Path $repoRoot "data\logo.ico"),
    (Join-Path $repoRoot "data\logo.png"),
    (Join-Path $repoRoot "build\version_info.txt")
)) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Required build input is missing: $requiredFile"
    }
}

foreach ($target in @($standaloneExe, $portableZip, $installer, $legacyStandalone, $pyinstallerWork)) {
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null

Write-Host "Building the self-contained Windows executable..."
Push-Location $repoRoot
try {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $distRoot `
        --workpath $pyinstallerWork `
        $specFile
    $pyinstallerExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $pyinstallerWork) {
        Remove-Item -LiteralPath $pyinstallerWork -Recurse -Force
    }
}
if ($pyinstallerExitCode -ne 0 -or -not (Test-Path -LiteralPath $standaloneExe)) {
    throw "PyInstaller failed to create $standaloneExe."
}

Compress-Archive -LiteralPath $standaloneExe -DestinationPath $portableZip -CompressionLevel Optimal
Write-Host "Built standalone EXE: $standaloneExe"
Write-Host "Built portable ZIP:  $portableZip"

if ($SkipInstaller) {
    Write-Host "Installer build skipped by request."
    return
}

$iscc = Find-InnoCompiler
if (-not $iscc) {
    $message = "Inno Setup 6 was not found. The standalone EXE and portable ZIP were built; the installer was skipped. Install Inno Setup 6 or set ISCC_PATH, then run build_release.bat again."
    if ($RequireInstaller) {
        throw $message
    }
    Write-Warning $message
    return
}

Write-Host "Building the Inno Setup installer with $iscc..."
& $iscc (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $installer)) {
    throw "Inno Setup failed to create $installer."
}
Write-Host "Built installer EXE:  $installer"
