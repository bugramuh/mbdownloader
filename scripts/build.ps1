# Builds MBDownloader for Windows: dist\MBDownloader\ (+ a zip next to it).
#   scripts\build.ps1                 build the app folder and the zip
#   scripts\build.ps1 -SkipTools      do not bundle yt-dlp/ffmpeg (the app can download them from Settings > Video)
# Requirements: Python 3.11+ (py launcher), internet access on the first run.
param([switch]$SkipTools, [switch]$NoZip, [string]$VenvDir = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venv = if ($VenvDir) { $VenvDir } else { Join-Path $root '.venv' }
if (-not (Test-Path "$venv\Scripts\python.exe")) { py -3 -m venv $venv }
$py = "$venv\Scripts\python.exe"
& $py -m pip install -q -r requirements.txt pyinstaller
if ($LASTEXITCODE) { throw 'pip failed' }

$version = (& $py -c "from mbdownloader import paths; print(paths.VERSION)").Trim()
if (-not $SkipTools) { & "$PSScriptRoot\fetch-tools.ps1" }
& "$PSScriptRoot\pack-firefox.ps1"

$build = Join-Path $root 'build\pyi'
$dist = Join-Path $root 'dist'
Remove-Item $dist -Recurse -Force -ErrorAction SilentlyContinue

# Qt modules the app never uses (keeps the bundle smaller)
$exclude = @('PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DExtras', 'PySide6.Qt3DAnimation',
    'PySide6.QtQuick3D', 'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtBluetooth', 'PySide6.QtNfc', 'PySide6.QtSensors', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
    'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtStateMachine',
    'PySide6.QtPositioning', 'PySide6.QtLocation', 'PySide6.QtSpatialAudio', 'PySide6.QtTextToSpeech', 'PySide6.QtWebSockets',
    'PySide6.QtHttpServer', 'PySide6.QtShaderTools', 'tkinter')
$excludeArgs = $exclude | ForEach-Object { '--exclude-module'; $_ }

# 1) native messaging host (console exe: the browser talks to it over stdin/stdout)
& $py -m PyInstaller --noconfirm --clean --onefile --console --name mbdownloader-native-host `
    --distpath "$build\host" --workpath "$build\work-host" --specpath "$build" `
    --exclude-module PySide6 --exclude-module tkinter "$root\native_host\mbdownloader_native_host.py"
if ($LASTEXITCODE) { throw 'host build failed' }

# 2) the app
$ico = Join-Path $root 'mbdownloader\resources\app.ico'
& $py -m PyInstaller --noconfirm --clean --windowed --name MBDownloader --icon $ico `
    --distpath $dist --workpath "$build\work-app" --specpath "$build" `
    --add-data "$root\mbdownloader\resources;mbdownloader\resources" @excludeArgs "$root\run.py"
if ($LASTEXITCODE) { throw 'app build failed' }

$app = Join-Path $dist 'MBDownloader'
Copy-Item "$build\host\mbdownloader-native-host.exe" $app
Copy-Item "$root\Extension" "$app\Extension" -Recurse
if (-not $SkipTools -and (Test-Path "$root\tools")) { Copy-Item "$root\tools" "$app\tools" -Recurse }
foreach ($x in @("mbdownloader-firefox-$version-signed.xpi", "mbdownloader-firefox-$version.xpi")) {
    $src = Join-Path $root "build\$x"
    if (Test-Path $src) {
        $name = if ($x -like '*signed*') { 'FirefoxExtension.xpi' } else { 'FirefoxExtension-unsigned.xpi' }
        Copy-Item $src (Join-Path $app $name)
    }
}
Copy-Item "$root\LICENSE", "$root\README.md" $app

$final = $app
Write-Host "OK $final\MBDownloader.exe"
if (-not $NoZip) {
    $zip = Join-Path $dist "MBDownloader-$version-windows-x64.zip"
    Compress-Archive -Path $final -DestinationPath $zip -Force
    Write-Host "OK $zip"
}
