"""Installs / updates yt-dlp, ffmpeg and deno from their official GitHub releases into %APPDATA%\\...\\bin.
Every download is checked against the SHA-256 the release publishes."""
import hashlib
import os
import re
import shutil
import subprocess
import threading
import zipfile

from PySide6.QtCore import QObject, Signal

from . import paths, video
from .downloader import DownloadFailure, SegmentedDownloader, describe_error, pool
from .i18n import _
from .qtutil import on_main

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
YTDLP_SUMS = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
DENO_SUMS = DENO_URL + ".sha256sum"
# LGPL build: enough for merging, remuxing and audio extraction, without GPL redistribution obligations.
FFMPEG_ASSET = "ffmpeg-master-latest-win64-lgpl.zip"
FFMPEG_URL = f"https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/{FFMPEG_ASSET}"
FFMPEG_SUMS = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256"

DENO_SIZE = "~45 MB"
FFMPEG_SIZE = "~170 MB"

CREATE_NO_WINDOW = 0x08000000


def _fetch_text(url: str) -> str:
    r = pool().request("GET", url, headers={"User-Agent": DEFAULT_UA}, redirect=True, retries=3, timeout=30)
    if r.status != 200:
        raise DownloadFailure(_("Could not fetch the checksum for verification."))
    return r.data.decode("utf-8", errors="replace")


def _expected_hash(sums: str, name: str | None) -> str:
    """Finds the SHA-256 for `name` in a checksum list (or the only hash in the file)."""
    for line in sums.splitlines():
        m = re.search(r"\b([0-9a-fA-F]{64})\b", line)
        if m and (name is None or name in line):
            return m.group(1).lower()
    raise DownloadFailure(_("Could not fetch the checksum for verification."))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _verify(path: str, sums_url: str, name: str | None):
    expected = _expected_hash(_fetch_text(sums_url), name)
    if _sha256(path) != expected:
        raise DownloadFailure(_("Checksum mismatch; the download was discarded."))


class ToolInstaller(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.busy = False
        self.progress = 0.0
        self.message: str | None = None
        self.ytdlp_version: str | None = None
        self.deno_version: str | None = None
        self.ffmpeg_version: str | None = None
        self._cache: dict[str, str] = {}

    def version_of(self, path: str) -> str | None:
        if path in self._cache:
            return self._cache[path]
        arg = "-version" if os.path.basename(path).lower().startswith("ffmpeg") else "--version"
        try:
            out = subprocess.run([path, arg], capture_output=True, timeout=20, env=video.environment(),
                                 creationflags=CREATE_NO_WINDOW).stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return None
        first = out.strip().splitlines()[0].strip() if out.strip() else ""
        if first.startswith("ffmpeg version"):
            first = "ffmpeg " + first.split()[2]
        if first:
            self._cache[path] = first
        return first or None

    def refresh_versions(self, custom: str):
        def work():
            y = video.locate_ytdlp(custom)
            d = video.locate_deno()
            f = video.locate_ffmpeg()
            yv = self.version_of(y) if y else None
            dv = self.version_of(d) if d else None
            fv = self.version_of(f) if f else None

            def done():
                self.ytdlp_version, self.deno_version, self.ffmpeg_version = yv, dv, fv
                self.changed.emit()
            on_main(done)
        threading.Thread(target=work, daemon=True).start()

    # --- Generic download + install ---

    def _install(self, label: str, url: str, temp_name: str, finalize, completion):
        """Downloads url into bin\\temp_name with the segmented downloader, then runs finalize(temp) on a worker
        thread; finalize returns a version string or raises."""
        if self.busy:
            if completion:
                completion(None, DownloadFailure(_("Another tool install is in progress.")))
            return
        self.busy = True
        self.progress = 0.0
        self.message = _("Downloading {0}…", label)
        self.changed.emit()
        bindir = paths.user_bin_dir()
        os.makedirs(bindir, exist_ok=True)
        tmp = os.path.join(bindir, temp_name)
        dl = SegmentedDownloader(url, {"User-Agent": DEFAULT_UA}, tmp, 4)
        total = {"n": -1}
        dl.on_probe = lambda p: total.__setitem__("n", p.total)

        def progress(received, _segs):
            if total["n"] > 0:
                def upd():
                    self.progress = received / total["n"]
                    self.changed.emit()
                on_main(upd)
        dl.on_progress = progress

        def finished(err):
            if err:
                _remove(tmp)
                on_main(lambda: self._finish(label, None, err, completion))
                return
            on_main(lambda: self._set_message(_("Verifying…")))
            try:
                v = finalize(tmp)
                on_main(lambda: self._finish(label, v, None, completion))
            except Exception as e:  # noqa: BLE001
                _remove(tmp)
                on_main(lambda: self._finish(label, None, e, completion))
        dl.on_finish = finished
        dl.start()

    def _set_message(self, m):
        self.message = m
        self.changed.emit()

    def _finish(self, label, version, err, completion):
        self.busy = False
        self.progress = 0.0
        if err:
            self.message = _("Install failed: {0}", describe_error(err))
        else:
            self.message = _("{0} installed.", version)
        self.changed.emit()
        if completion:
            completion(version, err)

    # --- yt-dlp ---

    def install_ytdlp(self, completion=None):
        def finalize(tmp):
            _verify(tmp, YTDLP_SUMS, "yt-dlp.exe")
            self._cache.pop(tmp, None)
            v = self.version_of(tmp)
            if not v:
                raise DownloadFailure(_("The downloaded yt-dlp binary could not be executed."))
            dest = os.path.join(paths.user_bin_dir(), "yt-dlp.exe")
            os.replace(tmp, dest)
            self._cache[dest] = v
            self.ytdlp_version = v
            return "yt-dlp " + v
        self._install("yt-dlp", YTDLP_URL, ".yt-dlp.download.exe", finalize, completion)

    # --- deno ---

    def install_deno(self, completion=None):
        def finalize(tmp):
            _verify(tmp, DENO_SUMS, None)
            staging = os.path.join(paths.user_bin_dir(), ".deno-unpack")
            shutil.rmtree(staging, ignore_errors=True)
            try:
                with zipfile.ZipFile(tmp) as z:
                    z.extract("deno.exe", staging)
            except (zipfile.BadZipFile, KeyError) as e:
                raise DownloadFailure(_("The deno archive could not be unpacked.")) from e
            finally:
                _remove(tmp)
            exe = os.path.join(staging, "deno.exe")
            v = self.version_of(exe)
            if not v or not v.startswith("deno "):
                shutil.rmtree(staging, ignore_errors=True)
                raise DownloadFailure(_("The downloaded deno binary could not be executed."))
            dest = os.path.join(paths.user_bin_dir(), "deno.exe")
            os.replace(exe, dest)
            shutil.rmtree(staging, ignore_errors=True)
            self._cache[dest] = v
            self.deno_version = v
            return v
        self._install("deno", DENO_URL, ".deno.zip", finalize, completion)

    # --- ffmpeg ---

    def install_ffmpeg(self, completion=None):
        def finalize(tmp):
            _verify(tmp, FFMPEG_SUMS, FFMPEG_ASSET)
            bindir = paths.user_bin_dir()
            try:
                with zipfile.ZipFile(tmp) as z:
                    for name in z.namelist():
                        base = name.rsplit("/", 1)[-1].lower()
                        if base in ("ffmpeg.exe", "ffprobe.exe"):
                            target = os.path.join(bindir, base + ".new")
                            with z.open(name) as src, open(target, "wb") as dst:
                                shutil.copyfileobj(src, dst)
            except zipfile.BadZipFile as e:
                raise DownloadFailure(_("The ffmpeg archive could not be unpacked.")) from e
            finally:
                _remove(tmp)
            new = os.path.join(bindir, "ffmpeg.exe.new")
            if not os.path.exists(new):
                raise DownloadFailure(_("The ffmpeg archive could not be unpacked."))
            for base in ("ffmpeg.exe", "ffprobe.exe"):
                if os.path.exists(os.path.join(bindir, base + ".new")):
                    os.replace(os.path.join(bindir, base + ".new"), os.path.join(bindir, base))
            dest = os.path.join(bindir, "ffmpeg.exe")
            self._cache.pop(dest, None)
            v = self.version_of(dest)
            if not v:
                raise DownloadFailure(_("The downloaded ffmpeg binary could not be executed."))
            self.ffmpeg_version = v
            return v
        self._install("ffmpeg", FFMPEG_URL, ".ffmpeg.zip", finalize, completion)


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        pass


installer: ToolInstaller | None = None
