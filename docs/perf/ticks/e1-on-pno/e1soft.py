#!/usr/bin/env python3
"""Replay cullVoices() on a run's recorded margins (fc0bc1e leniency ~ ceil(0.9 x starts)) under soft-cull variants.
  as is      soft: over >= 0 and last_over > 0 and over >= last_over
  consec     soft: over >= 0 and last_over >= 0 (two over-limit renders in a row)
  steady     as is, or over >= 0 in a render with no tick in it or the 3 before it (steady state, no transient)
  e1soft.py RUN_DIR [...]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe
for run in sys.argv[1:]:
    rs = [r for r in dzprobe.load_renders(os.path.join(run, "renders_all.bin")) if r["samples"]]
    play = [i for i, r in enumerate(rs) if r["flags"] & 1]
    t0 = rs[play[0]]["timer"]
    first = next(i for i in play if ((rs[i]["timer"] - t0) & 0xFFFFFFFF) >= 25 * 44100)
    secs = sum(rs[i]["samples"] for i in play if i >= first) / 44100
    out = []
    for mode in ("asis", "consec", "steady"):
        last = 0; h = s = 0; lasttick = -99
        for i, r in enumerate(rs):
            if r["ticks"]: lasttick = i
            sc = max(128 - r["margin"], 0)
            kind = None
            if sc >= 50:
                p = rs[i - 1] if i else r
                st = p["startedTick"] + p["startedPending"] if i else 0
                over = sc - 80 - (-(-9 * st // 10))
                if p["voices"] > 7:
                    if over >= 32: kind = "h"
                    elif over >= 0:
                        rising = last > 0 and over >= last
                        if mode == "asis" and rising: kind = "s"
                        if mode == "consec" and last >= 0: kind = "s"
                        if mode == "steady" and (rising or i - lasttick > 3): kind = "s"
                last = over
            if kind and i >= first and r["flags"] & 1:
                if kind == "h": h += 1
                else: s += 1
        out.append("%s %.1f/%.1f" % (mode, 5 * h / secs, 5 * s / secs))
    print("%-24s hard/soft events per 5 s: %s" % (os.path.basename(run.rstrip("/")), "  ".join(out)))
