# Packs the Chrome/Edge/Brave extension for the Web Store: build\mbdownloader-chrome-<version>.zip
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$version = (Get-Content (Join-Path $root 'Extension\manifest.json') -Raw | ConvertFrom-Json).version
New-Item -ItemType Directory -Force (Join-Path $root 'build') | Out-Null
$zip = Join-Path $root "build\mbdownloader-chrome-$version.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $root 'Extension\*') -DestinationPath $zip
Write-Host "OK $zip"
