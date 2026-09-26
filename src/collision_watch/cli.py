"""Daily run: fetch the catalog, screen the next 24 hours, validate against
SOCRATES, and write the files the website reads.

    uv run collision-watch --out out --cache .cache
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
from sgp4.api import Satrec, SatrecArray

from collision_watch.catalog import load_catalog, load_socrates
from collision_watch.screen import EARTH_RADIUS_KM, _jd_grid, naive_seconds_per_step, screen
from collision_watch.validate import compare

HOURS = 24.0
TOP_N = 50


def _constellation(name: str) -> str:
    """First word of the name ("STARLINK-3104" -> "STARLINK"). Megaconstellation
    operators coordinate passes between their own satellites, so those are
    counted separately from passes between different operators or with debris."""
    return name.split("-")[0].split()[0].upper()


def _same_constellation(a, b) -> bool:
    if a.kind != "payload" or b.kind != "payload":
        return False
    return _constellation(a.name) == _constellation(b.name)


def _altitude_histogram(objs, start_unix) -> dict:
    arr = SatrecArray([Satrec.twoline2rv(o.line1, o.line2) for o in objs])
    jd, fr = _jd_grid(start_unix, 1, 1.0)
    err, r, _ = arr.sgp4(jd, fr)
    ok = (err[:, 0] == 0) & np.isfinite(r[:, 0, 0])
    alt = np.linalg.norm(r[ok, 0], axis=1) - EARTH_RADIUS_KM
    edges = np.arange(200, 2050, 50)
    counts, _ = np.histogram(alt, bins=edges)
    return {
        "bin_km": 50,
        "bins": [{"from_km": int(lo), "count": int(c)} for lo, c in zip(edges[:-1], counts)],
        "above_2000_km": int((alt >= 2000).sum()),
        "geo": int(((alt > 35000) & (alt < 36500)).sum()),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("out"))
    p.add_argument("--cache", type=Path, default=Path(".cache"))
    p.add_argument("--hours", type=float, default=HOURS)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    objs, groups = load_catalog(args.cache)
    start = float(int(time.time()) // 60 * 60)
    print(f"[catalog] {len(objs)} objects {groups}")

    encounters, stats = screen(objs, start, hours=args.hours)
    naive_est = naive_seconds_per_step(objs, start) * stats.steps
    print(f"[screen] {len(encounters)} encounters in {stats.seconds:.1f}s (all-pairs est. {naive_est:.0f}s)")

    socrates_csv, socrates_fresh = load_socrates(args.cache)
    validation = compare(socrates_csv, objs, encounters, start, args.hours)
    validation["socrates_fresh"] = socrates_fresh
    print(f"[validate] {validation}")

    def obj(i):
        o = objs[i]
        return {"id": o.norad_id, "name": o.name, "kind": o.kind}

    cross = [e for e in encounters if not _same_constellation(objs[e.i], objs[e.j])]
    same = Counter(_constellation(objs[e.i].name) for e in encounters if _same_constellation(objs[e.i], objs[e.j]))

    latest = {
        "generated_at": int(time.time()),
        "window": {"start": int(start), "hours": args.hours},
        "catalog": {"objects": len(objs), "groups": groups, "by_kind": Counter(o.kind for o in objs)},
        "stats": {
            "steps": stats.steps,
            "dt_s": stats.dt_s,
            "candidate_pairs": stats.candidate_pairs,
            "naive_pairs": stats.naive_pairs,
            "seconds": round(stats.seconds, 1),
            "naive_seconds_est": round(naive_est),
        },
        "validation": validation,
        "encounters_total": len(encounters),
        "same_constellation": dict(same.most_common(5)),
        "encounters": [
            {
                "tca": int(start + e.tca),
                "a": obj(e.i),
                "b": obj(e.j),
                "miss_km": round(e.miss_km, 3),
                "speed_kms": round(e.speed_kms, 2),
                "alt_km": round(e.alt_km),
            }
            for e in cross[:TOP_N]
        ],
        "altitude": _altitude_histogram(objs, start),
    }
    (args.out / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))
    # Element sets for the website's globe (propagated in the browser).
    (args.out / "objects.tle").write_text("".join(f"{o.name}\n{o.line1}\n{o.line2}\n" for o in objs))
    print(f"[out] wrote {args.out}/latest.json and objects.tle")


if __name__ == "__main__":
    main()
