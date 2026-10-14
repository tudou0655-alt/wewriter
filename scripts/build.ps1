$ErrorActionPreference = 'Stop'
$version = '0.10.2'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    $releaseDir = Join-Path $projectRoot "dist\v$version"
    & '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --distpath $releaseDir --workpath (Join-Path $projectRoot "build\v$version") WeWriter.spec
    if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install.ps1') -Destination (Join-Path $releaseDir 'install.ps1') -Force
    Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $releaseDir 'README.md') -Force
    Compress-Archive -LiteralPath @((Join-Path $releaseDir 'WeWriter.exe'), (Join-Path $releaseDir 'install.ps1'), (Join-Path $releaseDir 'README.md')) -DestinationPath (Join-Path $projectRoot "dist\WeWriter-v$version-Windows.zip") -Force
} finally {
    Pop-Location
}
