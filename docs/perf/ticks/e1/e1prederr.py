#!/usr/bin/env python3
"""Render-time prediction error: actual render time (samples of time) minus RenderCost.predict(rendered size), with
the firmware's fit replayed from the ring (as in e1sim.py). By renders since the step tick, and for renders that
doubled (rendered > space). Also time left = queued - total time.
  e1prederr.py RUN_DIR [...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe, e1sim
CPS = 1e9 / 44100
def pct(v, p): return v[min(len(v) - 1, int(p * len(v)))]
for run in sys.argv[1:]:
    rs = [r for r in dzprobe.load_renders(os.path.join(run, "renders_all.bin")) if r["samples"]]
    rc = e1sim.RenderCost(); rows = []; step = -99
    for i, r in enumerate(rs):
        if r["ticks"] and r["startedTick"]: step = i
        err = r["renderCyc"] / CPS - rc.predict(r["samples"])
        left = 128 - r["space"] - r["totalCyc"] / CPS
        if i > len(rs) // 2 and r["flags"] & 1:
            rows.append((min(i - step, 3), r["samples"] > r["space"] + 4, err, left))
        rc.add(r["samples"], r["renderCyc"] / CPS)
    print("=== %s" % os.path.basename(run.rstrip("/")))
    for key, f in [("all", lambda x: True), ("doubled", lambda x: x[1]), ("dStep0", lambda x: x[0] == 0),
                   ("dStep1", lambda x: x[0] == 1), ("dStep2", lambda x: x[0] == 2), ("dStep3+ doubled", lambda x: x[0] == 3 and x[1])]:
        v = sorted(x[2] for x in rows if f(x)); l = sorted(x[3] for x in rows if f(x))
        if v:
            print("  %-16s n %6d  err p50 %5.1f p99 %5.1f p99.9 %5.1f max %5.1f | left min %6.1f p0.1 %6.1f p1 %6.1f" % (
                key, len(v), pct(v, .5), pct(v, .99), pct(v, .999), v[-1], l[0], pct(l, .001), pct(l, .01)))
