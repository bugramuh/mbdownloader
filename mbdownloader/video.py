"""yt-dlp wrapper. Prefers a user-installed copy (%APPDATA%\\...\\bin), then the bundled tools\\ folder, then PATH."""
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import cookies, fmt, paths
from .downloader import DownloadFailure
from .i18n import _

CREATE_NO_WINDOW = 0x08000000


def _first_file(candidates) -> str | None:
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def locate_ytdlp(custom: str = "") -> str | None:
    return _first_file([
        custom,
        os.path.join(paths.user_bin_dir(), "yt-dlp.exe"),
        os.path.join(paths.bundled_tools_dir(), "yt-dlp.exe"),
    ]) or shutil.which("yt-dlp")


def locate_ffmpeg() -> str | None:
    return _first_file([
        os.path.join(paths.user_bin_dir(), "ffmpeg.exe"),
        os.path.join(paths.bundled_tools_dir(), "ffmpeg.exe"),
    ]) or shutil.which("ffmpeg")


def locate_deno() -> str | None:
    """deno is the JavaScript runtime yt-dlp uses to solve YouTube's n/signature challenges."""
    return _first_file([
        os.path.join(paths.user_bin_dir(), "deno.exe"),
        os.path.join(paths.bundled_tools_dir(), "deno.exe"),
        os.path.join(os.path.expanduser("~"), ".deno", "bin", "deno.exe"),
    ]) or shutil.which("deno")


def is_youtube(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ("youtube.com", "youtu.be", "youtube-nocookie.com"))


def is_signed_in_client_failure(message: str) -> bool:
    """YouTube rejects the signed-in clients yt-dlp picks when cookies are passed (yt-dlp#17389)."""
    return "page needs to be reloaded" in message.lower()


def environment() -> dict:
    env = dict(os.environ)
    extra = [paths.user_bin_dir(), paths.bundled_tools_dir()]
    env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def kill_tree(proc: subprocess.Popen):
    """yt-dlp starts ffmpeg; terminate the whole tree so nothing keeps writing after pause/cancel."""
    if proc.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


@dataclass
class VideoProgress:
    downloaded: int
    total: int
    speed: float


_AUDIO_PART = re.compile(r"\.f[0-9]+[-a-z0-9]*\.(m4a|webm|opus|mp3|aac|ogg)$")
_VIDEO_PART = re.compile(r"\.f[0-9]+[-a-z0-9]*\.[a-z0-9]+$")


def stage_for(line: str) -> str | None:
    """Turns a yt-dlp log line into a user-facing phase description, if it marks a new phase."""
    if line.startswith("[download] Destination:"):
        lower = line.replace("[download] Destination: ", "").lower()
        if _AUDIO_PART.search(lower):
            return _("Downloading audio…")
        if _VIDEO_PART.search(lower):
            return _("Downloading video…")
        return _("Downloading…")
    if line.startswith("[Merger]"):
        return _("Merging video and audio (ffmpeg)…")
    if line.startswith("[VideoRemuxer]") or line.startswith("[VideoConvertor]"):
        return _("Converting container (ffmpeg)…")
    if line.startswith("[ExtractAudio]"):
        return _("Extracting audio (ffmpeg)…")
    if line.startswith("MCFMT|"):
        p = line.split("|")
        if len(p) < 6:
            return None
        parts = []
        if p[1] not in ("NA", ""):
            parts.append(p[1])
        if p[2] != "NA":
            parts.append(p[2].upper())
        if p[3] not in ("NA", "none") and p[3]:
            parts.append(p[3].split(".")[0])
        try:
            n = float(p[5])
            if n > 0:
                parts.append("~" + fmt.size(int(n)))
        except ValueError:
            pass
        return _("Selected: {0}", " · ".join(parts))
    if "has already been downloaded" in line:
        return _("File already downloaded")
    return None


def run(executable: str, url: str, output_dir: str, format_args: list, headers: dict, cookie_file: str | None,
        use_cookies: bool, on_progress, on_line, on_finish) -> subprocess.Popen | None:
    """Starts yt-dlp. Callbacks run on reader threads: on_progress(VideoProgress), on_line(str),
    on_finish(final_path_or_None, error_or_None)."""
    args = [
        executable,
        "--newline", "--no-playlist", "--no-colors", "--progress", "--no-simulate", "--no-mtime", "--no-quiet",
        "--encoding", "utf-8", "--windows-filenames",
        "-N", "4",
        "-o", os.path.join(output_dir, "%(title).150B [%(id)s].%(ext)s"),
        "--progress-template",
        "download:DMP|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.speed)s",
        "--print", "after_move:filepath",
        "--print", "before_dl:MCFMT|%(resolution)s|%(ext)s|%(vcodec)s|%(acodec)s|%(filesize_approx)s|%(format_id)s|%(duration)s",
    ]
    args += format_args
    ff = locate_ffmpeg()
    if ff:
        args += ["--ffmpeg-location", ff]
    ua = headers.get("User-Agent")
    if ua:
        args += ["--user-agent", ua]
    ref = headers.get("Referer")
    if ref:
        args += ["--referer", ref]
    if use_cookies:
        cf = cookies.merged_cookie_file(cookie_file, urlsplit(url).hostname)
        if cf:
            args += ["--cookies", cf]
    deno = locate_deno()
    if deno:
        args += ["--js-runtimes", "deno:" + deno]
    args.append(url)

    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                cwd=output_dir, env=environment(), creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        on_finish(None, e)
        return None

    state = {"final": None, "error": None}
    lock = threading.Lock()

    def handle(raw: str, is_error: bool):
        line = raw.strip()
        if not line:
            return
        if line.startswith("DMP|"):
            parts = line.split("|")
            if len(parts) < 5:
                return

            def num(s):
                try:
                    return int(float(s))
                except ValueError:
                    return -1
            total = num(parts[2])
            if total <= 0:
                total = num(parts[3])
            try:
                spd = max(0.0, float(parts[4]))
            except ValueError:
                spd = 0.0
            on_progress(VideoProgress(max(0, num(parts[1])), total, spd))
            return
        if os.path.isabs(line) and os.path.isfile(line):
            with lock:
                state["final"] = line
        if is_error and line.startswith("ERROR"):
            with lock:
                state["error"] = line
        on_line(line)

    def reader(stream, is_error):
        for raw in iter(stream.readline, b""):
            handle(raw.decode("utf-8", errors="replace"), is_error)
        stream.close()

    threads = [threading.Thread(target=reader, args=(proc.stdout, False), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, True), daemon=True)]
    for t in threads:
        t.start()

    def waiter():
        code = proc.wait()
        for t in threads:
            t.join(timeout=3)
        with lock:
            final, err = state["final"], state["error"]
        if code == 0:
            on_finish(final, None)
        else:
            on_finish(None, DownloadFailure(err or _("yt-dlp exited with code {0}", code)))

    threading.Thread(target=waiter, daemon=True).start()
    return proc
