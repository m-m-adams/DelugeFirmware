#!/usr/bin/env python3
"""Replay the direness dynamics on recorded margins, raising it only outside the kTickRecoveryRenders (3) renders after
a tick render, with the thresholds shifted down by SHIFT samples of margin. Static (better oscillators at lower
direness cost more render time, not modelled).  e1dire2.py RUN_DIR [...] [--shift 0 10 20]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe, e1sim
CPS = 1e9 / 44100
F = dzprobe.RENDER_FLAGS
args = sys.argv[1:]; shifts = [0]
if "--shift" in args:
    k = args.index("--shift"); shifts = [int(x) for x in args[k + 1:]]; args = args[:k]
for run in args:
    rs = [r for r in dzprobe.load_renders(os.path.join(run, "renders_all.bin")) if r["samples"]]
    play = [i for i, r in enumerate(rs) if r["flags"] & F["playing"]]
    t0 = rs[play[0]]["timer"]
    first = next(i for i in play if ((rs[i]["timer"] - t0) & 0xFFFFFFFF) >= 25 * 44100)
    # min-render margin (as in E1 (c)): queued - tick reserve - predict(min(space, time to the next tick))
    rc = e1sim.RenderCost(); mmin = []
    for i, r in enumerate(rs):
        tick = r["ticks"] > 0
        til = r["samples"] if (not tick and i + 1 < len(rs) and rs[i + 1]["ticks"]) else 9999
        mmin.append(128 - r["space"] - int((r["tickCost"] / 16 if tick else 0) + rc.predict(min(r["space"], til))))
        rc.add(r["samples"], r["renderCyc"] / CPS)
    print("=== %s" % os.path.basename(run.rstrip("/")))
    for excl, usemin in ((False, False), (True, False), (False, True), (True, True)):
        for s in shifts:
            dire, tchg, hist, n, since = 0, 0, {}, 0, 99
            for i, r in enumerate(rs):
                since = 0 if r["ticks"] else since + 1
                scaled = max(128 - ((mmin[i] if usemin else r["margin"]) + s), 0)
                if scaled >= 50:
                    if not (excl and since <= 3):
                        nd = min(scaled - 49, 14)
                        if nd >= dire:
                            dire, tchg = nd, r["timer"]
                elif scaled < 47 and ((r["timer"] - tchg) & 0xFFFFFFFF) >= 5512:
                    tchg = r["timer"]; dire = max(dire - 1, 0)
                if i >= first and r["flags"] & F["playing"]:
                    hist[dire] = hist.get(dire, 0) + 1; n += 1
            print("  %-24s shift %2d: >12 %3.0f%% mean %4.1f | %s" % (("skip tick dips" if excl else "as is") + (", min render" if usemin else ""), s,
                  100 * sum(c for d, c in hist.items() if d > 12) / n, sum(d * c for d, c in hist.items()) / n,
                  " ".join("%d:%.0f%%" % (d, 100 * c / n) for d, c in sorted(hist.items()) if c / n >= 0.01)))
