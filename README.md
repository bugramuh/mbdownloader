# MBDownloader

A Windows download manager based on [mstcvk/mc-download-manager](https://github.com/mstcvk/mc-download-manager) (macOS, Swift/SwiftUI), MIT licensed.
Captures Chrome, Edge, Brave, Opera and Firefox downloads, accelerates them with up to 16 parallel connections and downloads
videos from web pages with yt-dlp. The interface is in **English** and **Turkish** (follows the Windows display language).

The browser extension (`Extension/`), the bridge protocol and the download engine behaviour are the same as upstream; the app
itself is rewritten in Python with PySide6 (Qt) because SwiftUI/AppKit do not exist on Windows.

## What maps to what

| macOS app | Windows port |
|---|---|
| SwiftUI windows, menu-bar item | PySide6 windows, system-tray icon |
| `URLSession` segmented downloader | `urllib3` + threads: probe, `Range` segments, 429/503 throttling, single-stream fallback, resume |
| Network.framework bridge on `127.0.0.1:48213` | `http.server` on the same port, same `/ping /download /downloads /action /site` API and `Origin`/`Host` checks |
| Native messaging host (`open -g`) | `mbdownloader-native-host.exe`, registered in `HKCU\Software\...\NativeMessagingHosts` for Chrome, Edge, Brave, Chromium, Firefox |
| Quarantine attribute | `Zone.Identifier` (Mark of the Web) so SmartScreen checks downloaded files |
| Move to Trash | Recycle Bin |
| `SMAppService` login item | `HKCU\...\Run` entry |
| `WKWebView` site login | QtWebEngine window, cookies exported for yt-dlp |
| Safari extension | not applicable (Safari is not available on Windows) |
| Bundled yt-dlp / ffmpeg / deno | yt-dlp + ffmpeg bundled by the build, or installed from Settings ▸ Video (SHA-256 verified); deno on demand |

## Running from source

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python run.py
```

## Building

```powershell
scripts\build.ps1            # dist\MBDownloader\MBDownloader.exe + a zip
scripts\build.ps1 -SkipTools # without bundled yt-dlp/ffmpeg
```

## Browser extension setup

Run the setup wizard from the first-launch dialog or *Settings ▸ Browser Extensions*:

1. **Chrome / Edge / Brave / Opera:** the wizard copies the extension to `Downloads\MBDownloader\Browser Plugins\<Browser>`;
   open the extensions page, enable *Developer mode*, choose *Load unpacked* and select that folder.
2. **Firefox:** `scripts\pack-firefox.ps1` builds the add-on; release Firefox needs it signed by Mozilla (set `AMO_JWT_ISSUER` and
   `AMO_JWT_SECRET` before building). Unsigned, it can be loaded temporarily from `about:debugging`.

If the app is not running, the extension starts it through the native host and hands the download over once it is up.

## Data locations

* Settings, download list, cookies, user-installed tools: `%APPDATA%\MBDownloader\`
* Default download folder: `Downloads\MBDownloader\` (category subfolders optional)

## License

MIT, like upstream (see `LICENSE`). yt-dlp is Unlicense; the ffmpeg build used by default is the LGPL variant of BtbN/FFmpeg-Builds.
