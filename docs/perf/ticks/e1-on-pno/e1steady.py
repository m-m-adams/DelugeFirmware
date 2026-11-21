#!/usr/bin/env python3
"""Steady-state margin from the firmware's RenderCost fit: with fixed cost f and per-sample cost p (samples of time),
renders that just keep up settle at s* = (f + g) / (1 - p) samples (g = time between renders not in the fit), and leave
m* = 128 - s* - (f + p s*) queued. Prints m* by renders since the step, per run.   e1steady.py RUN_DIR [--gap 2]"""
import os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe, e1sim
CPS = 1e9 / 44100
args = sys.argv[1:]; gap = 2.0
if "--gap" in args:
    k = args.index("--gap"); gap = float(args[k + 1]); del args[k:k + 2]
for run in args:
    rs = [r for r in dzprobe.load_renders(os.path.join(run, "renders_all.bin")) if r["samples"]]
    rc = e1sim.RenderCost(); step = -99; by = collections.defaultdict(list); lines = collections.defaultdict(list)
    for i, r in enumerate(rs):
        if r["ticks"] and (r["startedTick"] + r["refused"]) >= 4: step = i
        line = rc._line()
        if line and i > len(rs) // 3 and r["flags"] & 1:
            f, p = line
            s = (f + gap) / (1 - p) if p < 1 else 999
            m = 128 - s - (f + p * s)
            d = min(i - step, 25)
            key = "d%d" % d if d < 6 else ("d6-24" if d < 25 else "d25+")
            by[key].append(m); lines[key].append((f, p))
        rc.add(r["samples"], r["renderCyc"] / CPS)
    print("=== %s" % os.path.basename(run.rstrip("/")))
    for k in sorted(by, key=lambda k: (len(k), k)):
        v = sorted(by[k]); fp = lines[k]
        print("  %-6s n %6d  m* p1 %6.1f p10 %6.1f p50 %6.1f | f %.1f p %.3f" % (
            k, len(v), v[len(v) // 100], v[len(v) // 10], v[len(v) // 2],
            sum(x[0] for x in fp) / len(fp), sum(x[1] for x in fp) / len(fp)))
