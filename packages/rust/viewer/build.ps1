# Builds the viewer: compiles openskp-viewer to wasm32 and embeds it (base64)
# into a single self-contained dist/openskp-viewer.html.
Set-Location $PSScriptRoot

# cargo reports progress on stderr; Windows PowerShell would turn that into
# errors under 'Stop', so only switch to 'Stop' after the build.
cargo build --release --target wasm32-unknown-unknown
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$ErrorActionPreference = 'Stop'

$wasm = [IO.File]::ReadAllBytes("$PSScriptRoot\target\wasm32-unknown-unknown\release\openskp_viewer.wasm")
$html = [IO.File]::ReadAllText("$PSScriptRoot\web\index.html")
$out = $html.Replace('__WASM_BASE64__', [Convert]::ToBase64String($wasm))

New-Item -ItemType Directory -Force "$PSScriptRoot\dist" | Out-Null
[IO.File]::WriteAllText("$PSScriptRoot\dist\openskp-viewer.html", $out, (New-Object Text.UTF8Encoding $false))
Write-Host "dist/openskp-viewer.html ($([math]::Round($out.Length / 1KB)) KB)"
