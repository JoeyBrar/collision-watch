"""Where deep-space spacecraft are, from JPL Horizons. Writes out/deep.json."""

import argparse
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from collision_watch.catalog import _get

HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
STEP_H = 6
DAYS = 3

# horizons id, name, one line about it
CRAFT = [
    (-31, "voyager 1", "launched 1977, the farthest human-made object"),
    (-32, "voyager 2", "launched 1977, the only craft to visit uranus and neptune"),
    (-98, "new horizons", "flew past pluto in 2015, now in the kuiper belt"),
    (-96, "parker solar probe", "dives closer to the sun than anything before it"),
    (-170, "james webb", "infrared telescope 1.5 million km from earth"),
    (-61, "juno", "orbiting jupiter since 2016"),
    (-159, "europa clipper", "on its way to jupiter's moon europa, arrives 2030"),
    (-28, "juice", "esa, on its way to jupiter's moons, arrives 2031"),
    (-255, "psyche", "headed for a metal-rich asteroid, arrives 2029"),
    (-49, "lucy", "touring the asteroids that share jupiter's orbit"),
    (-121, "bepicolombo", "esa/jaxa, arriving at mercury"),
    (-143205, "tesla roadster", "launched on the first falcon heavy in 2018, still orbiting the sun"),
]


def _vectors(hid: int, start: datetime) -> list[list[float]]:
    stop = datetime.fromtimestamp(start.timestamp() + DAYS * 86400, timezone.utc)
    q = {
        "format": "json",
        "COMMAND": f"'{hid}'",
        "EPHEM_TYPE": "VECTORS",
        "CENTER": "'500@399'",  # geocenter
        "REF_PLANE": "FRAME",  # earth equator, same axes as the satellites
        "START_TIME": f"'{start:%Y-%m-%d %H:%M}'",
        "STOP_TIME": f"'{stop:%Y-%m-%d %H:%M}'",
        "STEP_SIZE": f"'{STEP_H} h'",
        "VEC_TABLE": "1",
        "OUT_UNITS": "KM-S",
        "CSV_FORMAT": "YES",
        "OBJ_DATA": "NO",
    }
    result = json.loads(_get(f"{HORIZONS}?{urllib.parse.urlencode(q)}"))["result"]
    block = re.search(r"\$\$SOE\n(.*?)\$\$EOE", result, re.S)
    if not block:
        raise ValueError(result[:200])
    rows = [line.split(",") for line in block.group(1).strip().splitlines()]
    return [[round(float(v)) for v in r[2:5]] for r in rows]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("out"))
    p.add_argument("--cache", type=Path, default=Path(".cache"))
    args = p.parse_args()

    cached = {}
    cache_file = args.cache / "deep.json"
    if cache_file.exists():
        cached = {c["id"]: c for c in json.loads(cache_file.read_text())["craft"]}

    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def one(entry):
        hid, name, note = entry
        try:
            km = _vectors(hid, start)
            return {"id": hid, "name": name, "note": note, "t0": int(start.timestamp()), "step_s": STEP_H * 3600, "km": km}
        except (OSError, ValueError, KeyError) as e:
            print(f"[deep] {name}: {e}")
            return cached.get(hid)

    # horizons turns away parallel requests
    craft = [c for c in map(one, CRAFT) if c]

    out = {"generated_at": int(time.time()), "craft": craft}
    text = json.dumps(out, separators=(",", ":"))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "deep.json").write_text(text)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(text)
    print(f"[deep] {len(craft)}/{len(CRAFT)} spacecraft")


if __name__ == "__main__":
    main()
