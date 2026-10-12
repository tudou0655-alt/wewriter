# Per-user installation. Does not require administrator rights or enable autostart.
$ErrorActionPreference = 'Stop'
$sourceExe = Join-Path $PSScriptRoot 'WeWriter.exe'
if (-not (Test-Path -LiteralPath $sourceExe)) { throw 'Put install.ps1 next to WeWriter.exe before running it.' }
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\WeWriter'
New-Item -ItemType Directory -Path $installDir -Force | Out-Null
Copy-Item -LiteralPath $sourceExe -Destination (Join-Path $installDir 'WeWriter.exe') -Force
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'WeWriter.lnk'))
$shortcut.TargetPath = Join-Path $installDir 'WeWriter.exe'
$shortcut.WorkingDirectory = $installDir
$shortcut.IconLocation = (Join-Path $installDir 'WeWriter.exe') + ',0'
$shortcut.Description = 'WeChat article collection and writing workspace'
$shortcut.Save()
Write-Host "Installed: $installDir"
Write-Host 'Use the WeWriter desktop shortcut to launch. Configure API keys inside the app.'
