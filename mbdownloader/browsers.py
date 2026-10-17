"""Browser extension helpers. Guided setup for Google Chrome, Microsoft Edge, Brave, Opera and Firefox."""
import os
import shutil

from . import paths, winshell

KINDS = ["chrome", "edge", "brave", "opera", "firefox"]
CHROMIUM_KINDS = ["chrome", "edge", "brave", "opera"]

NAMES = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "brave": "Brave", "opera": "Opera", "firefox": "Firefox"}
EXTENSIONS_PAGES = {"chrome": "chrome://extensions", "edge": "edge://extensions", "brave": "brave://extensions",
                    "opera": "opera://extensions", "firefox": "about:addons"}

# Set this once the extension is published on the Chrome Web Store; the wizard then links to it.
CHROME_WEB_STORE_URL: str | None = None


def is_installed(kind: str) -> bool:
    return winshell.browser_exe(kind) is not None


def installed_browsers() -> list[str]:
    return [k for k in KINDS if is_installed(k)]


def plugins_root() -> str:
    """%USERPROFILE%\\Downloads\\MBDownloader\\Browser Plugins"""
    return os.path.join(paths.default_download_dir(), "Browser Plugins")


def plugin_folder(kind: str) -> str:
    return os.path.join(plugins_root(), NAMES[kind])


def prepare_files(kind: str) -> str:
    """Copies the unpacked extension into the browser's plugin folder. Returns the folder."""
    dest = plugin_folder(kind)
    os.makedirs(plugins_root(), exist_ok=True)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(paths.bundled_extension_dir(), dest, ignore=shutil.ignore_patterns(".DS_Store"))
    page = EXTENSIONS_PAGES[kind]
    toggle = "Developer mode (left sidebar)" if kind == "edge" else "Developer mode (top right)"
    readme = (f"MBDownloader — {NAMES[kind]} extension\r\n\r\n"
              f"1. Open {page} in {NAMES[kind]}.\r\n"
              f"2. Turn on \"{toggle}\".\r\n"
              "3. Click \"Load unpacked\" and select this folder.\r\n\r\n"
              "Do not move or delete this folder while the extension is installed.\r\n")
    try:
        with open(os.path.join(dest, "README.txt"), "w", encoding="utf-8") as f:
            f.write(readme)
    except OSError:
        pass
    return dest


def files_prepared(kind: str) -> bool:
    return os.path.exists(os.path.join(plugin_folder(kind), "manifest.json"))


def open_extensions_page(kind: str):
    winshell.open_in_browser(kind, EXTENSIONS_PAGES[kind])


# --- Firefox ---

def signed_firefox_addon() -> str | None:
    """Add-on signed by Mozilla (unlisted channel). Firefox installs it permanently after its own prompt."""
    p = os.path.join(paths.app_dir(), "FirefoxExtension.xpi")
    return p if os.path.exists(p) else None


def unsigned_firefox_addon() -> str | None:
    p = os.path.join(paths.app_dir(), "FirefoxExtension-unsigned.xpi")
    return p if os.path.exists(p) else None


def install_firefox_addon():
    xpi = signed_firefox_addon()
    if xpi:
        winshell.open_in_browser("firefox", xpi)


def prepare_temporary_firefox_addon() -> str:
    src = unsigned_firefox_addon()
    if not src:
        raise FileNotFoundError("FirefoxExtension-unsigned.xpi")
    d = plugin_folder("firefox")
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, "mbdownloader.xpi")
    shutil.copyfile(src, dest)
    return dest


def open_firefox_debugging():
    winshell.open_in_browser("firefox", "about:debugging#/runtime/this-firefox")
