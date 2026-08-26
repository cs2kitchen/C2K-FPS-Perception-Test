param(
    [switch]$SkipInstall,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$appName = "C2K FPS Perception Test"
$distRoot = Join-Path $repoRoot "dist"
$standalone = Join-Path $distRoot $appName
$portableZip = Join-Path $distRoot "$appName Portable.zip"
$installer = Join-Path $distRoot "$appName Setup.exe"
$pyinstallerWork = Join-Path $PSScriptRoot "pyinstaller"
$specFile = Join-Path $PSScriptRoot "$appName.spec"

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
}
else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python 3.12 or newer was not found."
    }
    $python = $pythonCommand.Source
}

foreach ($target in @($standalone, $portableZip, $installer, $pyinstallerWork, $specFile)) {
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null

Push-Location $repoRoot
try {
    if (-not $SkipInstall) {
        & $python -m pip install --upgrade pip
        & $python -m pip install -r requirements-build.txt
    }

    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name $appName `
        --icon (Join-Path $repoRoot "data\logo.ico") `
        --version-file (Join-Path $repoRoot "build\version_info.txt") `
        --paths $repoRoot `
        --add-data "$(Join-Path $repoRoot 'data\logo.png');data" `
        --distpath $distRoot `
        --workpath $pyinstallerWork `
        --specpath $PSScriptRoot `
        (Join-Path $repoRoot "app\main.py")

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed."
    }

    Compress-Archive -Path (Join-Path $standalone "*") -DestinationPath $portableZip -CompressionLevel Optimal

    if (-not $SkipInstaller) {
        $isccCandidates = @(@(
            $env:ISCC_PATH,
            (Join-Path $repoRoot ".tools\Inno Setup 6\ISCC.exe"),
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) })
        if (-not $isccCandidates) {
            throw "Inno Setup 6 was not found. Install it or set ISCC_PATH."
        }
        & $isccCandidates[0] (Join-Path $PSScriptRoot "installer.iss")
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup failed."
        }
    }
}
finally {
    Pop-Location
}

Write-Host "Built $standalone"
Write-Host "Built $portableZip"
if (-not $SkipInstaller) {
    Write-Host "Built $installer"
}

foreach ($generated in @($pyinstallerWork, $specFile)) {
    if (Test-Path -LiteralPath $generated) {
        Remove-Item -LiteralPath $generated -Recurse -Force
    }
}
