# Authenticode-sign files with signtool. Used by build.ps1 -Sign and by WeWriter.spec for bundled DLLs.
# No secret lives in this repository; pick ONE signing method with environment variables:
#   Azure Trusted Signing : WEWRITER_TS_DLIB (path to Azure.CodeSigning.Dlib.dll) and
#                           WEWRITER_TS_METADATA (path to metadata.json); sign in first with `az login`.
#   Certificate           : WEWRITER_SIGN_THUMBPRINT (SHA1 thumbprint of a code-signing certificate in
#                           the Windows certificate store, e.g. on a USB token or cloud HSM).
# Optional: WEWRITER_SIGNTOOL (path to signtool.exe), WEWRITER_TIMESTAMP_URL.
# See docs/代码签名与智能应用控制.md.
param(
    [string[]]$Files = @(),
    [string]$ListFile,
    [switch]$OnlyUnsigned,
    [switch]$Check
)
$ErrorActionPreference = 'Stop'

function Get-SignTool {
    if ($env:WEWRITER_SIGNTOOL) {
        if (-not (Test-Path -LiteralPath $env:WEWRITER_SIGNTOOL)) { throw "WEWRITER_SIGNTOOL not found: $env:WEWRITER_SIGNTOOL" }
        return $env:WEWRITER_SIGNTOOL
    }
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $found = Get-ChildItem -Path $kits -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $found) { throw 'signtool.exe not found. Install the Windows SDK "Signing Tools" or set WEWRITER_SIGNTOOL.' }
    return $found.FullName
}

function Get-SignArgs {
    if ($env:WEWRITER_TS_DLIB -or $env:WEWRITER_TS_METADATA) {
        foreach ($p in @($env:WEWRITER_TS_DLIB, $env:WEWRITER_TS_METADATA)) {
            if (-not $p -or -not (Test-Path -LiteralPath $p)) { throw 'Trusted Signing needs both WEWRITER_TS_DLIB and WEWRITER_TS_METADATA pointing at existing files.' }
        }
        $ts = if ($env:WEWRITER_TIMESTAMP_URL) { $env:WEWRITER_TIMESTAMP_URL } else { 'http://timestamp.acs.microsoft.com' }
        return @('sign', '/fd', 'SHA256', '/tr', $ts, '/td', 'SHA256', '/dlib', $env:WEWRITER_TS_DLIB, '/dmdf', $env:WEWRITER_TS_METADATA)
    }
    if ($env:WEWRITER_SIGN_THUMBPRINT) {
        $ts = if ($env:WEWRITER_TIMESTAMP_URL) { $env:WEWRITER_TIMESTAMP_URL } else { 'http://timestamp.digicert.com' }
        return @('sign', '/fd', 'SHA256', '/sha1', $env:WEWRITER_SIGN_THUMBPRINT, '/tr', $ts, '/td', 'SHA256')
    }
    throw 'No signing method configured: set WEWRITER_TS_DLIB + WEWRITER_TS_METADATA, or WEWRITER_SIGN_THUMBPRINT.'
}

$signtool = Get-SignTool
$signArgs = Get-SignArgs
if ($Check) { Write-Host "Signing configured: $signtool"; exit 0 }

if ($ListFile) { $Files += Get-Content -LiteralPath $ListFile -Encoding UTF8 | Where-Object { $_.Trim() } }
$Files = @($Files | ForEach-Object { (Resolve-Path -LiteralPath $_).Path })
if ($OnlyUnsigned) {
    # Files already carrying a valid signature (python3xx.dll, Microsoft runtimes, node.exe) keep it.
    $Files = @($Files | Where-Object { (Get-AuthenticodeSignature -LiteralPath $_).Status -ne 'Valid' })
}
if (-not $Files.Count) { Write-Host 'Nothing to sign.'; exit 0 }
Write-Host "Signing $($Files.Count) file(s)..."
for ($i = 0; $i -lt $Files.Count; $i += 40) {
    $batch = $Files[$i..([Math]::Min($i + 39, $Files.Count - 1))]
    & $signtool @signArgs @batch
    if ($LASTEXITCODE -ne 0) { throw "signtool failed (exit $LASTEXITCODE)" }
}
$bad = @($Files | Where-Object { (Get-AuthenticodeSignature -LiteralPath $_).Status -ne 'Valid' })
if ($bad.Count) { throw ('Signature not valid after signing: ' + ($bad -join ', ')) }
Write-Host "Signed and verified $($Files.Count) file(s)."
