"""All-vs-all conjunction screening over a propagation window.

Every object is propagated with SGP4 on a fixed time grid (dt seconds). At
each step a KD-tree over the positions returns only the pairs that are close
enough to possibly come within `threshold_km` before the next sample:

    search radius = threshold + v_rel_max * dt / 2

(two objects closing at up to v_rel_max can cover at most v_rel_max * dt/2 of
extra distance between a sample and the true closest approach). Each candidate
pair gets a linear time-of-closest-approach estimate from its relative position
and velocity, and pairs that pass are refined with full SGP4 to the exact TCA.

Pairs moving together (docked vehicles, satellites flying in formation, GEO
co-location) are not crossings and are skipped via `min_speed_kms`.
"""

import time
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.spatial import cKDTree
from sgp4.api import Satrec, SatrecArray, jday

from collision_watch.catalog import Obj

V_REL_MAX = 16.0  # km/s, above any head-on closing speed in Earth orbit
EARTH_RADIUS_KM = 6378.137


@dataclass
class Encounter:
    i: int
    j: int
    tca: float  # seconds from window start
    miss_km: float
    speed_kms: float
    alt_km: float = 0.0


@dataclass
class ScreenStats:
    objects: int
    steps: int
    dt_s: float
    candidate_pairs: int  # pair checks actually made
    naive_pairs: int  # pair checks an all-pairs comparison would make
    seconds: float


def _jd_grid(start_unix: float, n: int, dt: float):
    t = time.gmtime(start_unix)
    jd0, fr0 = jday(t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)
    fr = fr0 + np.arange(n) * dt / 86400.0
    return np.full(n, jd0), fr


def screen(
    objs: list[Obj],
    start_unix: float,
    hours: float = 24.0,
    dt: float = 10.0,
    threshold_km: float = 5.0,
    min_speed_kms: float = 1.0,
    chunk: int = 60,
) -> tuple[list[Encounter], ScreenStats]:
    t0 = time.perf_counter()
    sats = [Satrec.twoline2rv(o.line1, o.line2) for o in objs]
    arr = SatrecArray(sats)
    n_steps = int(hours * 3600 / dt)
    jd, fr = _jd_grid(start_unix, n_steps, dt)
    radius = threshold_km + V_REL_MAX * dt / 2

    hits_i, hits_j, hits_t, hits_d = [], [], [], []
    candidates = 0
    naive = 0
    for k0 in range(0, n_steps, chunk):
        k1 = min(k0 + chunk, n_steps)
        err, r, v = arr.sgp4(jd[k0:k1], fr[k0:k1])
        for s in range(k1 - k0):
            ok = np.flatnonzero((err[:, s] == 0) & np.isfinite(r[:, s, 0]))
            pos = r[ok, s]
            naive += len(ok) * (len(ok) - 1) // 2
            pairs = cKDTree(pos).query_pairs(radius, output_type="ndarray")
            if not len(pairs):
                continue
            candidates += len(pairs)
            a, b = ok[pairs[:, 0]], ok[pairs[:, 1]]
            dr = r[b, s] - r[a, s]
            dv = v[b, s] - v[a, s]
            dv2 = np.einsum("ij,ij->i", dv, dv)
            moving = dv2 > min_speed_kms**2
            a, b, dr, dv, dv2 = a[moving], b[moving], dr[moving], dv[moving], dv2[moving]
            # Linear closest approach within half a step of this sample.
            tstar = np.clip(-np.einsum("ij,ij->i", dr, dv) / dv2, -dt / 2, dt / 2)
            dmin = np.linalg.norm(dr + dv * tstar[:, None], axis=1)
            close = dmin < threshold_km * 1.5  # margin; exact check after refinement
            if close.any():
                hits_i.append(a[close])
                hits_j.append(b[close])
                hits_t.append((k0 + s) * dt + tstar[close])
                hits_d.append(dmin[close])

    encounters = _refine(sats, _collapse(hits_i, hits_j, hits_t, hits_d, dt), jd[0], fr[0], dt, threshold_km)
    stats = ScreenStats(len(objs), n_steps, dt, candidates, naive, time.perf_counter() - t0)
    return encounters, stats


def _collapse(hits_i, hits_j, hits_t, hits_d, dt) -> list[tuple[int, int, float]]:
    """The same encounter is usually caught at a couple of adjacent samples;
    keep one estimate per (pair, pass). Passes of one pair are an orbit apart."""
    if not hits_i:
        return []
    i, j, t, d = (np.concatenate(x) for x in (hits_i, hits_j, hits_t, hits_d))
    order = np.lexsort((t, j, i))
    i, j, t, d = i[order], j[order], t[order], d[order]
    out = []
    start = 0
    for k in range(1, len(i) + 1):
        if k == len(i) or i[k] != i[start] or j[k] != j[start] or t[k] - t[k - 1] > 3 * dt:
            best = start + int(np.argmin(d[start:k]))
            out.append((int(i[best]), int(j[best]), float(t[best])))
            start = k
    return out


def _state(sat: Satrec, jd0: float, fr0: float, t: float):
    e, r, v = sat.sgp4(jd0, fr0 + t / 86400.0)
    return e, np.asarray(r), np.asarray(v)


def _refine(sats, candidates, jd0, fr0, dt, threshold_km) -> list[Encounter]:
    out = []
    for i, j, t_est in candidates:

        def dist(t):
            e1, r1, _ = _state(sats[i], jd0, fr0, t)
            e2, r2, _ = _state(sats[j], jd0, fr0, t)
            return np.inf if e1 or e2 else float(np.linalg.norm(r2 - r1))

        res = minimize_scalar(dist, bounds=(t_est - dt, t_est + dt), method="bounded", options={"xatol": 1e-3})
        if not np.isfinite(res.fun) or res.fun >= threshold_km:
            continue
        _, r1, v1 = _state(sats[i], jd0, fr0, res.x)
        _, r2, v2 = _state(sats[j], jd0, fr0, res.x)
        out.append(
            Encounter(
                i,
                j,
                float(res.x),
                float(res.fun),
                float(np.linalg.norm(v2 - v1)),
                float(np.linalg.norm((r1 + r2) / 2) - EARTH_RADIUS_KM),
            )
        )
    out.sort(key=lambda e: e.miss_km)
    return out


def naive_seconds_per_step(objs: list[Obj], start_unix: float, threshold_km: float = 5.0) -> float:
    """Time one all-pairs distance check at a single step (the baseline the
    KD-tree replaces), for the benchmark line."""
    sats = SatrecArray([Satrec.twoline2rv(o.line1, o.line2) for o in objs])
    jd, fr = _jd_grid(start_unix, 1, 1.0)
    err, r, _ = sats.sgp4(jd, fr)
    pos = r[(err[:, 0] == 0) & np.isfinite(r[:, 0, 0]), 0]
    t0 = time.perf_counter()
    close = 0
    for k in range(0, len(pos), 512):  # chunked so memory stays bounded
        d = np.linalg.norm(pos[k : k + 512, None, :] - pos[None, :, :], axis=2)
        close += int((d < threshold_km).sum())
    return time.perf_counter() - t0
