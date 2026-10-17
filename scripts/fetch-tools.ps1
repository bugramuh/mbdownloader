# Downloads the yt-dlp and ffmpeg binaries that get bundled into the app (into tools\).
# Sources:
#   yt-dlp : https://github.com/yt-dlp/yt-dlp/releases (yt-dlp.exe, checksum verified)
#   ffmpeg : https://github.com/BtbN/FFmpeg-Builds (win64 LGPL build, checksum verified)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$tools = Join-Path $root 'tools'
New-Item -ItemType Directory -Force $tools | Out-Null

function Get-ExpectedHash($sumsUrl, $name) {
    $text = (Invoke-WebRequest -UseBasicParsing $sumsUrl).Content
    if ($text -is [byte[]]) { $text = [Text.Encoding]::UTF8.GetString($text) }   # served as octet-stream
    foreach ($line in ($text -split "`n")) {
        if ((-not $name -or $line -match [regex]::Escape($name)) -and $line -match '\b([0-9a-fA-F]{64})\b') { return $Matches[1].ToLower() }
    }
    throw "No checksum for $name in $sumsUrl"
}

function Test-Hash($file, $expected) {
    $actual = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLower()
    if ($actual -ne $expected) { Remove-Item $file -Force; throw "Checksum mismatch for $file" }
}

$ytdlp = Join-Path $tools 'yt-dlp.exe'
if (-not (Test-Path $ytdlp)) {
    Write-Host '-> downloading yt-dlp...'
    Invoke-WebRequest -UseBasicParsing 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' -OutFile $ytdlp
    Test-Hash $ytdlp (Get-ExpectedHash 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS' 'yt-dlp.exe')
}

$ffmpeg = Join-Path $tools 'ffmpeg.exe'
if (-not (Test-Path $ffmpeg)) {
    Write-Host '-> downloading ffmpeg (LGPL build)...'
    $asset = 'ffmpeg-master-latest-win64-lgpl.zip'
    $base = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest'
    $zip = Join-Path $tools $asset
    Invoke-WebRequest -UseBasicParsing "$base/$asset" -OutFile $zip
    Test-Hash $zip (Get-ExpectedHash "$base/checksums.sha256" $asset)
    $tmp = Join-Path $tools '_ffmpeg'
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip $tmp
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $tools -Force
    Remove-Item $tmp, $zip -Recurse -Force
}

Write-Host 'Tools ready:'
& $ytdlp --version
(& $ffmpeg -version)[0]
