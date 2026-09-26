"""Compare our encounters against CelesTrak's SOCRATES conjunction report.

SOCRATES screens the full public catalog over 7 days (5 km threshold) from
somewhat older element sets, so only its events that fall inside our window,
involve two objects we screened, and are real crossings (>= min_speed_kms)
are comparable. A SOCRATES event counts as matched when we report the same
pair with a TCA within `tca_tol_s` seconds.
"""

import csv
import io
from datetime import datetime, timezone

from collision_watch.catalog import Obj
from collision_watch.screen import Encounter


def _unix(tca: str) -> float:
    return datetime.strptime(tca, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()


def compare(
    socrates_csv: str,
    objs: list[Obj],
    encounters: list[Encounter],
    start_unix: float,
    hours: float,
    min_speed_kms: float = 1.0,
    tca_tol_s: float = 60.0,
    top: int = 20,
) -> dict:
    ids = {o.norad_id for o in objs}
    end = start_unix + hours * 3600
    ref = []
    for row in csv.DictReader(io.StringIO(socrates_csv)):
        a, b = int(row["NORAD_CAT_ID_1"]), int(row["NORAD_CAT_ID_2"])
        t = _unix(row["TCA"])
        if a in ids and b in ids and start_unix <= t < end and float(row["TCA_RELATIVE_SPEED"]) >= min_speed_kms:
            ref.append((min(a, b), max(a, b), t, float(row["TCA_RANGE"])))
    ref.sort(key=lambda x: x[3])

    ours: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for e in encounters:
        a, b = objs[e.i].norad_id, objs[e.j].norad_id
        ours.setdefault((min(a, b), max(a, b)), []).append((start_unix + e.tca, e.miss_km))

    def match(ev):
        cands = [m for t, m in ours.get((ev[0], ev[1]), []) if abs(t - ev[2]) <= tca_tol_s]
        return min(cands) if cands else None

    matched = [(ev, match(ev)) for ev in ref]
    hits = [(ev, m) for ev, m in matched if m is not None]
    top_ref = matched[:top]
    return {
        "comparable_events": len(ref),
        "matched": len(hits),
        "top": len(top_ref),
        "top_matched": sum(1 for _, m in top_ref if m is not None),
        "median_miss_diff_km": (
            sorted(abs(ev[3] - m) for ev, m in hits)[len(hits) // 2] if hits else None
        ),
        "tca_tolerance_s": tca_tol_s,
    }
