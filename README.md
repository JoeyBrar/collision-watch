# collision-watch

Screens every publicly tracked satellite and debris object for close approaches
over the next 24 hours, once a day. Results show up at
[joeybrar.github.io/space](https://joeybrar.github.io/space/).

## How it works

1. **Catalog.** Element sets (TLEs) for every object on orbit come from
   [Space-Track.org](https://www.space-track.org) (USSPACECOM) when
   `SPACETRACK_USER` / `SPACETRACK_PASSWORD` are set; otherwise all active
   satellites plus the largest debris groups from [CelesTrak](https://celestrak.org).
2. **Propagation.** Every object is propagated with SGP4 every 10 seconds for 24
   hours, in vectorized batches.
3. **Screening.** Comparing every pair at every step is ~10^8 checks per step. A
   KD-tree over each step's positions returns only pairs within
   `threshold + v_max * dt / 2` of each other (how far two objects can close
   before the next sample), which cuts the pair checks by several orders
   of magnitude.
4. **Refinement.** Candidates get a linear time-of-closest-approach estimate from
   relative position and velocity; survivors are refined with full SGP4 to the
   exact closest approach, miss distance, and relative speed. Pairs moving
   together (docked vehicles, formation flying, GEO co-location) are skipped.
5. **Validation.** Results are compared against CelesTrak's
   [SOCRATES](https://celestrak.org/SOCRATES/) report: for each SOCRATES event
   inside the window between two screened objects, did we find the same pair
   within 60 s of the same time? SOCRATES only screens active satellites against
   everything, so debris-vs-debris encounters found here have no reference.

## Run it

```
uv run collision-watch --out out --cache .cache
```

Writes `out/latest.json` (stats, validation, closest approaches, altitude
histogram) and `out/objects.tle` (element sets for the site's globe).

A GitHub Action runs this daily at 07:00 UTC and force-pushes the output to the
`data` branch.

## Limits

- TLE/SGP4 accuracy is on the order of a kilometer, so miss distances are
  screening estimates, not collision probabilities.
- Without Space-Track credentials, the CelesTrak groups cover roughly 60% of
  tracked objects.

Orbital data: USSPACECOM via [Space-Track.org](https://www.space-track.org) and
[CelesTrak](https://celestrak.org).
