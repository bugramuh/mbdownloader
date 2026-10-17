# Packs the WebExtension for Firefox: build\firefox\ (unpacked) + build\mbdownloader-firefox-<version>.xpi
#
# Firefox shares the code in Extension\ with Chrome; only the manifest differs:
#   - background.scripts instead of background.service_worker (Firefox has no extension service workers)
#   - browser_specific_settings.gecko (add-on id, minimum version, data collection declaration)
#
# Release Firefox only installs add-ons signed by Mozilla. With AMO API credentials (AMO_JWT_ISSUER / AMO_JWT_SECRET,
# https://addons.mozilla.org/developers/addon/api/key/) the xpi is signed as an unlisted add-on (once per version)
# and written to build\mbdownloader-firefox-<version>-signed.xpi.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$geckoId = 'mbdownloader@mbdownloader.app'
$manifest = Get-Content (Join-Path $root 'Extension\manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $manifest.version
$stage = Join-Path $root 'build\firefox'
$xpi = Join-Path $root "build\mbdownloader-firefox-$version.xpi"
$signed = Join-Path $root "build\mbdownloader-firefox-$version-signed.xpi"

Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item (Join-Path $root 'Extension\*') $stage -Recurse -Exclude manifest.json

$manifest.background = [pscustomobject]@{ scripts = @('background.js') }
$manifest | Add-Member -NotePropertyName browser_specific_settings -NotePropertyValue ([pscustomobject]@{
    gecko = [pscustomobject]@{
        id = $geckoId
        strict_min_version = '140.0'
        data_collection_permissions = [pscustomobject]@{ required = @('none') }
    }
}) -Force
$json = $manifest | ConvertTo-Json -Depth 10
[IO.File]::WriteAllText((Join-Path $stage 'manifest.json'), $json, (New-Object Text.UTF8Encoding $false))

# A .xpi is a zip with forward-slash entry names; Windows PowerShell's Compress-Archive writes backslashes, so use .NET.
Remove-Item $xpi -Force -ErrorAction SilentlyContinue
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$fs = [IO.File]::Open($xpi, 'Create')
$zip = New-Object IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create)
Get-ChildItem $stage -Recurse -File | ForEach-Object {
    $name = $_.FullName.Substring($stage.Length + 1).Replace('\', '/')
    [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $name)
}
$zip.Dispose(); $fs.Dispose()
Write-Host "OK $xpi"

if (Test-Path $signed) { Write-Host "OK $signed (already signed)"; return }
if ($env:AMO_JWT_ISSUER -and $env:AMO_JWT_SECRET) {
    Write-Host 'Signing with addons.mozilla.org (unlisted)'
    $out = Join-Path $root 'build\firefox-signed'
    Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $out | Out-Null
    $env:WEB_EXT_API_KEY = $env:AMO_JWT_ISSUER
    $env:WEB_EXT_API_SECRET = $env:AMO_JWT_SECRET
    npx --yes web-ext@8 sign --source-dir $stage --artifacts-dir $out --channel unlisted
    Move-Item (Get-ChildItem "$out\*.xpi" | Select-Object -First 1).FullName $signed
    Remove-Item $out -Recurse -Force
    Write-Host "OK $signed"
} else {
    Write-Host '(not signed: no AMO credentials; release Firefox can only load it temporarily)'
}
