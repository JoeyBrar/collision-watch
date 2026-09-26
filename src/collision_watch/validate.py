"""Compare our encounters against CelesTrak's SOCRATES conjunction report.

SOCRATES screens the full public catalog over 7 days (5 km threshold) from
somewhat older element sets, so only its events that fall inside our window,
involve two objects we screened, and are real crossings (>= min_speed_kms)
are comparable. A SOCRATES event counts as matched when we report the same
pair with a TCA within `tca_tol_s` seconds.

SOCRATES' element sets are a day or two older than ours, and orbits move in
that time (Starlink maneuvers, drag), so many of its events simply aren't close
approaches anymore. Each run audits a sample of the unmatched events: using our
element sets, how close does the pair actually get near SOCRATES' TCA? If that
is under the threshold, the screen missed it; otherwise the data moved on.
"""

import csv
import io
import random
import time
from datetime import datetime, timezone

import numpy as np
from scipy.optimize import minimize_scalar
from sgp4.api import Satrec, jday

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
    threshold_km: float = 5.0,
    audit_n: int = 200,
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
    missed = [ev for ev, m in matched if m is None]
    audit = _audit(random.Random(0).sample(missed, min(audit_n, len(missed))), objs, threshold_km)
    return {
        "comparable_events": len(ref),
        "matched": len(hits),
        "top": len(top_ref),
        "top_matched": sum(1 for _, m in top_ref if m is not None),
        "median_miss_diff_km": (
            sorted(abs(ev[3] - m) for ev, m in hits)[len(hits) // 2] if hits else None
        ),
        "tca_tolerance_s": tca_tol_s,
        "audit": audit,
    }


def _audit(events, objs: list[Obj], threshold_km: float) -> dict:
    sats = {o.norad_id: Satrec.twoline2rv(o.line1, o.line2) for o in objs}

    def pos(sat, t):
        g = time.gmtime(t)
        jd, fr = jday(g.tm_year, g.tm_mon, g.tm_mday, g.tm_hour, g.tm_min, g.tm_sec + t % 1)
        e, r, _ = sat.sgp4(jd, fr)
        return None if e else np.asarray(r)

    def dist(a, b, t):
        ra, rb = pos(sats[a], t), pos(sats[b], t)
        return np.inf if ra is None or rb is None else float(np.linalg.norm(ra - rb))

    closest = []
    for a, b, t, _ in events:
        res = minimize_scalar(lambda x: dist(a, b, x), bounds=(t - 120, t + 120), method="bounded")
        closest.append(res.fun)
    closest = [c for c in closest if np.isfinite(c)]
    return {
        "sampled": len(closest),
        "screen_missed": sum(1 for c in closest if c < threshold_km),
        "median_separation_km": round(float(np.median(closest)), 1) if closest else None,
    }
