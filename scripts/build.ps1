# -Sign: Authenticode-sign the EXE and its bundled DLLs/PYDs (configure scripts/sign.ps1 first).
# Unsigned builds are blocked by Windows 11 Smart App Control on customer PCs.
param([switch]$Sign)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    # Single version source: wewriter/__init__.py (__version__). Never hard-code it here.
    $version = (& $python -c "from wewriter import __version__; print(__version__)" | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $version) { throw 'Cannot read wewriter.__version__' }
    $releaseDir = Join-Path $projectRoot "dist\v$version"
    $zipPath = Join-Path $projectRoot "dist\WeWriter-v$version-Windows.zip"
    # The manifest lives in the ignored build\ directory, outside PyInstaller's cleaned workpath.
    $buildInfo = Join-Path $projectRoot "build\release-info\v$version\build-info.json"
    & $python scripts\verify_delivery.py --write-build-info $buildInfo
    if ($LASTEXITCODE -ne 0) { throw 'Build manifest failed' }
    $env:WEWRITER_BUILD_INFO = $buildInfo
    $signScript = Join-Path $PSScriptRoot 'sign.ps1'
    if ($Sign) {
        & $signScript -Check
        if ($LASTEXITCODE -ne 0) { throw 'Signing is not configured' }
        $env:WEWRITER_SIGN = '1'
    }
    & $python -m PyInstaller --noconfirm --clean --distpath $releaseDir --workpath (Join-Path $projectRoot "build\v$version") WeWriter.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install.ps1') -Destination (Join-Path $releaseDir 'install.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $releaseDir 'README.md') -Force
    Copy-Item -LiteralPath $buildInfo -Destination (Join-Path $releaseDir 'build-info.json') -Force
    if ($Sign) {
        # signtool signs portable executable formats, not ordinary PowerShell source files.
        & $signScript -Files @((Join-Path $releaseDir 'WeWriter.exe'))
        if ($LASTEXITCODE -ne 0) { throw 'Signing failed' }
    } else {
        Write-Warning 'UNSIGNED build: Windows 11 Smart App Control will block WeWriter.exe on customer PCs. Use -Sign for customer delivery.'
    }
    Compress-Archive -LiteralPath @((Join-Path $releaseDir 'WeWriter.exe'), (Join-Path $releaseDir 'install.ps1'), (Join-Path $releaseDir 'README.md'), (Join-Path $releaseDir 'build-info.json')) -DestinationPath $zipPath -Force
    & $python scripts\verify_delivery.py --check-package $releaseDir --zip $zipPath
    if ($LASTEXITCODE -ne 0) { throw 'Package check failed' }
} finally {
    Remove-Item Env:WEWRITER_BUILD_INFO -ErrorAction SilentlyContinue
    Remove-Item Env:WEWRITER_SIGN -ErrorAction SilentlyContinue
    Pop-Location
}
