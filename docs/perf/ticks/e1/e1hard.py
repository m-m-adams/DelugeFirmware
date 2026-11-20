#!/usr/bin/env python3
"""Replay cullVoices() on a run's recorded margins, as is and with the hard cull judged on the margin alone (no
+20 per voice started). Static.  e1hard.py RUN_DIR [...]"""
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
    for hardabs in (False, True):
        last = 0; c = {"hard": 0, "soft": 0, "min": 0}
        for i, r in enumerate(rs):
            sc = max(128 - r["margin"], 0)
            kind = None
            if sc >= 50:
                p = rs[i - 1] if i else r
                st = p["startedTick"] + p["startedPending"] if i else 0
                over = sc - 80 - 20 * st
                overh = sc - 80 if hardabs else over
                if p["voices"] > 7:
                    if overh >= 32: kind = "hard"
                    elif over >= 0 and last > 0 and over >= last: kind = "soft"
                elif over >= 40: kind = "min"
                last = over
            if kind and i >= first and r["flags"] & 1: c[kind] += 1
        out.append("%s hard %.1f soft %.1f /5 s" % ("margin-only hard" if hardabs else "as is", 5 * c["hard"] / secs, 5 * c["soft"] / secs))
    print("%-24s %s | %s" % (os.path.basename(run.rstrip("/")), out[0], out[1]))
