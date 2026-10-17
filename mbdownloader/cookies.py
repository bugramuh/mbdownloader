"""Netscape cookie files for yt-dlp: per-download files (from the extension) and the shared site-login file."""
import os
import uuid

from . import paths


def cookies_dir() -> str:
    d = os.path.join(paths.support_dir(), "cookies")
    os.makedirs(d, exist_ok=True)
    return d


def site_cookie_file() -> str:
    return os.path.join(cookies_dir(), "sites.txt")


def site_cookie_path() -> str | None:
    p = site_cookie_file()
    return p if os.path.exists(p) else None


def netscape_lines(cookies) -> list[str]:
    """cookies: iterable of dicts with name, value, domain, path, secure, expirationDate."""
    lines = ["# Netscape HTTP Cookie File", "# Exported by MBDownloader"]
    for c in cookies:
        name, value, domain = c.get("name"), c.get("value"), c.get("domain")
        if not isinstance(name, str) or not isinstance(value, str) or not isinstance(domain, str) or not domain:
            continue
        path = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        try:
            exp = int(float(c.get("expirationDate") or 0))
        except (TypeError, ValueError):
            exp = 0
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append("\t".join([domain, flag, path, secure, str(exp), name, value]))
    return lines


def write_cookie_file(path: str, cookies) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(netscape_lines(cookies)) + "\n")
        return True
    except OSError:
        return False


def site_cookie_status() -> tuple[int, list[str], float | None]:
    """(cookie count, registrable domains, saved-at timestamp) of the site-login file."""
    p = site_cookie_file()
    try:
        with open(p, encoding="utf-8") as f:
            rows = [r for r in f.read().split("\n") if r and not r.startswith("#")]
        mtime = os.path.getmtime(p)
    except OSError:
        return 0, [], None
    hosts = set()
    for r in rows:
        h = r.split("\t", 1)[0].lstrip(".")
        parts = h.split(".")
        hosts.add(".".join(parts[-2:]) if len(parts) >= 2 else h)
    return len(rows), sorted(hosts), mtime


def site_domains() -> list[str]:
    return site_cookie_status()[1]


def _host_matches(host: str, domains: list[str]) -> bool:
    host = (host or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def has_cookies(item_cookies: str | None, target_host: str | None) -> bool:
    if item_cookies and os.path.exists(item_cookies):
        return True
    return _host_matches(target_host or "", site_domains())


def merged_cookie_file(item_cookies: str | None, target_host: str | None) -> str | None:
    """Combines the per-download cookie file with the site-login file (only for hosts the user signed in to)."""
    item = item_cookies if item_cookies and os.path.exists(item_cookies) else None
    site = site_cookie_path() if _host_matches(target_host or "", site_domains()) else None
    if not item and not site:
        return None
    if item and not site:
        return item
    if site and not item:
        return site
    try:
        body = open(site, encoding="utf-8").read() + "\n" + open(item, encoding="utf-8").read()
        tmp = os.path.join(cookies_dir(), f"merged-{uuid.uuid4()}.txt")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        return tmp
    except OSError:
        return item
