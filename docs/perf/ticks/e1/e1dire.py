#!/usr/bin/env python3
"""Replay setDireness()'s direness dynamics on a run's recorded margins with the direness thresholds shifted down by
SHIFT samples of margin (static: the extra render cost of the better oscillators etc. at lower direness isn't modelled).

  e1dire.py RUN_DIR [--shift 0 10 20 30] [--skip 25]

Firmware: scaled = 128 - margin; direness rises at once to min(scaled - 49, 14) when scaled >= 50 (margin <= 78), and
falls by 1 per 1/8 s while scaled < 47. A shift of S moves both thresholds S samples of margin lower.
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe
F = dzprobe.RENDER_FLAGS

ap = argparse.ArgumentParser()
ap.add_argument("run"); ap.add_argument("--shift", type=int, nargs="+", default=[0, 10, 20, 30])
ap.add_argument("--skip", type=float, default=25)
a = ap.parse_args()
for name in ("renders_all.bin", "renders.bin"):
    p = os.path.join(a.run, name)
    if os.path.exists(p):
        rs = [r for r in dzprobe.load_renders(p) if r["samples"]]
        break
play = [i for i, r in enumerate(rs) if r["flags"] & F["playing"]]
t0 = rs[play[0]]["timer"]
first = next(i for i in play if ((rs[i]["timer"] - t0) & 0xFFFFFFFF) >= a.skip * 44100)
print("=== %s" % os.path.basename(a.run.rstrip("/")))
for s in a.shift:
    dire, tchg, hist, n = 0, 0, {}, 0
    for i, r in enumerate(rs):
        scaled = max(128 - (r["margin"] + s), 0)
        if scaled >= 50:
            nd = min(scaled - 49, 14)
            if nd >= dire:
                dire, tchg = nd, r["timer"]
        elif scaled < 47 and ((r["timer"] - tchg) & 0xFFFFFFFF) >= 5512:
            tchg = r["timer"]; dire = max(dire - 1, 0)
        if i >= first and r["flags"] & F["playing"]:
            hist[dire] = hist.get(dire, 0) + 1; n += 1
    gt12 = sum(c for d, c in hist.items() if d > 12) / n
    print("  shift %2d (rise at margin <= %d, 14 at <= %d): direness>12 %3.0f%% | %s" % (
        s, 78 - s, 65 - s, 100 * gt12, " ".join("%d:%.0f%%" % (d, 100 * c / n) for d, c in sorted(hist.items()) if c / n >= 0.01)))
