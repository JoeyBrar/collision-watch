"""Fetch and parse public orbital element sets (TLEs) from CelesTrak.

CelesTrak refuses to re-serve a group it served to the same client within the
last ~2 hours ("GP data has not updated since your last successful download"),
so every successful download is cached and the cache is used when a request is
refused. A day-old TLE is still fine for a 24-hour screen.
"""

import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GP_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
SOCRATES_URL = "https://celestrak.org/SOCRATES/sort-minRange.csv"

# Active satellites plus the largest public debris groups. The full catalog
# (~30k objects incl. all debris and rocket bodies) needs a Space-Track account.
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
            out.append(Obj(int(l1[2:7]), name.strip(), l1, l2))
    return out


def load_catalog(cache_dir: Path) -> tuple[list[Obj], dict]:
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
