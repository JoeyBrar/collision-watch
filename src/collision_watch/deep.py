"""Where deep-space spacecraft are (JPL Horizons) and who's in space (Launch Library 2).
Writes out/deep.json and out/people.json."""

import argparse
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from collision_watch.catalog import _get

HORIZONS = "https://ssd.jpl.nasa.gov/api/horizons.api"
LL2 = "https://ll.thespacedevs.com/2.3.0"
STATIONS = {"International Space Station": "iss", "Tiangong space station": "tiangong"}
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
    # mars
    (-76, "curiosity", "rover in gale crater since 2012"),
    (499, "perseverance", "rover in jezero crater since 2021"),  # horizons ends at landing, so use mars
    (-74, "mars reconnaissance orbiter", "orbiting mars since 2006"),
    (-53, "mars odyssey", "orbiting mars since 2001"),
    (-41, "mars express", "esa, orbiting mars since 2003"),
    (-143, "exomars tgo", "esa/roscosmos, orbiting mars since 2016"),
    (-62, "hope", "uae, orbiting mars since 2021"),
    # moon
    (-85, "lunar reconnaissance orbiter", "mapping the moon since 2009"),
    (-152, "chandrayaan-2", "isro, orbiting the moon since 2019"),
    (-155, "danuri", "korea's lunar orbiter, since 2022"),
    (301, "apollo landers", "six landing sites and three rovers left on the moon, 1969-72"),
    # sun-earth l1 and l2
    (-21, "soho", "watching the sun from l1 since 1996"),
    (-92, "ace", "measuring the solar wind at l1 since 1997"),
    (-78, "dscovr", "photographs the whole sunlit earth from l1"),
    (-8, "wind", "solar wind at l1 since 1994"),
    (-156, "aditya-l1", "isro's solar observatory at l1"),
    (-680, "euclid", "esa, mapping dark matter from l2"),
    # orbiting the sun
    (-139479, "gaia", "esa, mapped two billion stars, retired 2025"),
    (-144, "solar orbiter", "esa, first close-up views of the sun's poles"),
    (-234, "stereo-a", "watching the sun from ahead of earth"),
    (-64, "osiris-apex", "brought back bennu samples, now headed for apophis"),
    (-37, "hayabusa2", "jaxa, returned ryugu samples, on to another asteroid"),
    (-227, "kepler", "found thousands of exoplanets, retired 2018"),
    (-79, "spitzer", "infrared telescope, retired 2020"),
    # leaving the solar system
    (-23, "pioneer 10", "launched 1972, last heard from in 2003"),
    (-24, "pioneer 11", "launched 1973, last heard from in 1995"),
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


def people() -> dict:
    """crew by station, plus anyone up there outside a station expedition"""
    exps = json.loads(_get(f"{LL2}/expeditions/?is_active=true&mode=detailed"))["results"]
    aboard: dict[str, list[str]] = {}
    for e in exps:
        key = STATIONS.get((e.get("spacestation") or {}).get("name"), "other")
        aboard.setdefault(key, []).extend(c["astronaut"]["name"] for c in e.get("crew", []))
    listed = {n for names in aboard.values() for n in names}
    up = json.loads(_get(f"{LL2}/astronauts/?in_space=true&limit=50&mode=list"))["results"]
    other = [a["name"] for a in up if a["name"] not in listed]
    if other:
        aboard.setdefault("other", []).extend(other)
    return {"generated_at": int(time.time()), "aboard": aboard}


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

    try:
        text = json.dumps(people(), separators=(",", ":"))
        (args.out / "people.json").write_text(text)
        (args.cache / "people.json").write_text(text)
        print(f"[people] {text}")
    except (OSError, ValueError, KeyError) as e:
        print(f"[people] {e}")
        if (args.cache / "people.json").exists():
            (args.out / "people.json").write_text((args.cache / "people.json").read_text())


if __name__ == "__main__":
    main()
