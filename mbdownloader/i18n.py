"""English (base) + Turkish strings. The interface follows the Windows display language (MBD_LANG overrides it).

Strings are English templates with {0}-style fields: _("Connections: {0}", n).
tr_upstream.json is converted from the macOS app's Localizable.xcstrings; tr_windows.json holds the
strings that are new or reworded for Windows.
"""
import ctypes
import json
import os

from .paths import resource_path

_table: dict[str, str] = {}
lang = "en"


def _system_language() -> str:
    env = os.environ.get("MBD_LANG")
    if env:
        return env.lower()[:2]
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if langid & 0x3FF == 0x1F:  # LANG_TURKISH
            return "tr"
    except Exception:
        pass
    return "en"


def init() -> None:
    global lang, _table
    lang = _system_language()
    _table = {}
    if lang != "tr":
        return
    for name in ("tr_upstream.json", "tr_windows.json"):
        try:
            with open(resource_path(name), encoding="utf-8") as f:
                _table.update(json.load(f))
        except (OSError, ValueError):
            pass


def _(text: str, *args) -> str:
    t = _table.get(text, text)
    try:
        return t.format(*args)
    except (IndexError, KeyError, ValueError):
        return text.format(*args)


def decimal_sep() -> str:
    return "," if lang == "tr" else "."
