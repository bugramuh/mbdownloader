"""Sizes, speeds and durations for the UI (binary units, like Windows Explorer)."""
import time

from .i18n import _, decimal_sep


def _num(value: float, decimals: int) -> str:
    s = f"{value:.{decimals}f}"
    return s.replace(".", decimal_sep())


def size(n: int) -> str:
    if n < 0:
        return "?"
    if n < 1024:
        return _("{0} bytes", n)
    for unit, decimals in (("KB", 0), ("MB", 1), ("GB", 2), ("TB", 2)):
        n /= 1024
        if n < 1024 or unit == "TB":
            return f"{_num(n, decimals)} {unit}"
    return "?"


def speed(bps: float) -> str:
    return _("{0}/s", size(int(bps)))


def duration(t: float) -> str:
    s = int(round(t))
    if s < 60:
        return _("{0} s", s)
    if s < 3600:
        return _("{0} min {1} s", s // 60, s % 60)
    return _("{0} h {1} min", s // 3600, (s % 3600) // 60)


def relative(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return _("just now")
    if d < 3600:
        return _("{0} min ago", int(d // 60))
    if d < 86400:
        return _("{0} h ago", int(d // 3600))
    if d < 2 * 86400:
        return _("yesterday")
    if d < 30 * 86400:
        return _("{0} days ago", int(d // 86400))
    return date_time(ts, with_time=False)


def date_time(ts: float, with_time: bool = True) -> str:
    lt = time.localtime(ts)
    return time.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y", lt)
