"""Fetch and parse orbital element sets (TLEs): the full catalog from
Space-Track when credentials are set, otherwise public groups from CelesTrak.

CelesTrak refuses to re-serve a group it served to the same client within the
last ~2 hours ("GP data has not updated since your last successful download"),
so every successful download is cached and the cache is used when a request is
refused. A day-old TLE is still fine for a 24-hour screen.
"""

import http.cookiejar
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
SOCRATES_URL = "https://celestrak.org/SOCRATES/sort-minRange.csv"

# The full catalog (every object on orbit, incl. all debris and rocket bodies)
# comes from Space-Track when SPACETRACK_USER / SPACETRACK_PASSWORD are set.
# Space-Track allows at most one GP query per hour; this runs once a day.
# Redistribution is allowed with citation (USSPACECOM via Space-Track.org).
SPACETRACK_LOGIN = "https://www.space-track.org/ajaxauth/login"
SPACETRACK_GP = (
    "https://www.space-track.org/basicspacedata/query/class/gp/decay_date/null-val"
    "/epoch/%3Enow-10/orderby/norad_cat_id/format/3le"
)

# Without an account: active satellites plus the largest public debris groups
# from CelesTrak.
GROUPS = ["active", "fengyun-1c-debris", "cosmos-2251-debris", "iridium-33-debris", "cosmos-1408-debris"]

NOT_UPDATED = "GP data has not updated"


@dataclass(frozen=True)
class Obj:
    norad_id: int
    name: str
    line1: str
    line2: str

    @property
    def kind(self) -> str:
        if " DEB" in self.name:
            return "debris"
        if "R/B" in self.name:
            return "rocket body"
        return "payload"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "collision-watch (github.com/JoeyBrar/collision-watch)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch(url: str, cache_file: Path, valid) -> tuple[str, bool]:
    """Download url, falling back to cache_file if the download is refused or
    invalid. Returns (text, fresh)."""
    try:
        text = _get(url)
        if valid(text):
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(text)
            return text, True
    except OSError as e:
        print(f"[fetch] {url}: {e}")
    if cache_file.exists():
        return cache_file.read_text(), False
    raise RuntimeError(f"no data for {url} and no cached copy")


def parse_tles(text: str) -> list[Obj]:
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out = []
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            # Space-Track's 3le format prefixes the name line with "0 ".
            out.append(Obj(int(l1[2:7]), name.removeprefix("0 ").strip(), l1, l2))
    return out


def _spacetrack_text(user: str, password: str) -> str:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", "collision-watch (github.com/JoeyBrar/collision-watch)")]
    body = urllib.parse.urlencode({"identity": user, "password": password}).encode()
    with opener.open(SPACETRACK_LOGIN, body, timeout=60) as resp:
        if b"Failed" in resp.read():
            raise OSError("space-track login failed")
    with opener.open(SPACETRACK_GP, timeout=300) as resp:
        return resp.read().decode("utf-8", "replace")


def load_catalog(cache_dir: Path) -> tuple[list[Obj], dict]:
    user, password = os.environ.get("SPACETRACK_USER"), os.environ.get("SPACETRACK_PASSWORD")
    if user and password:
        cache_file = cache_dir / "spacetrack.tle"
        try:
            text = _spacetrack_text(user, password)
            if "\n1 " not in text:
                raise OSError("space-track returned no element sets")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(text)
            fresh = True
        except OSError as e:
            print(f"[fetch] space-track: {e}")
            if not cache_file.exists():
                print("[fetch] no cached space-track data; falling back to celestrak")
                return _load_celestrak(cache_dir)
            text, fresh = cache_file.read_text(), False
        objs = parse_tles(text)
        return objs, {"space-track": {"objects": len(objs), "fresh": fresh}}
    return _load_celestrak(cache_dir)


def _load_celestrak(cache_dir: Path) -> tuple[list[Obj], dict]:
    objs: dict[int, Obj] = {}
    status = {}
    for group in GROUPS:
        text, fresh = fetch(
            GP_URL.format(group=group),
            cache_dir / f"{group}.tle",
            lambda t: not t.startswith(NOT_UPDATED) and "\n1 " in t,
        )
        group_objs = parse_tles(text)
        status[group] = {"objects": len(group_objs), "fresh": fresh}
        for o in group_objs:
            objs.setdefault(o.norad_id, o)
        time.sleep(1)  # be polite to CelesTrak
    return list(objs.values()), status


def load_socrates(cache_dir: Path) -> tuple[str, bool]:
    return fetch(SOCRATES_URL, cache_dir / "socrates.csv", lambda t: t.startswith("NORAD_CAT_ID_1"))
