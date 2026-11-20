#!/usr/bin/env python3
"""Renders that finished with less than N samples left in the codec buffer (queued at start - total time), per 5 s,
split by dStep and doubled / not.  e1neg.py RUN_DIR [...] [--below 0]"""
import os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe
CPS = 1e9 / 44100
args = sys.argv[1:]; below = 0.0
if "--below" in args:
    k = args.index("--below"); below = float(args[k + 1]); del args[k:k + 2]
for run in args:
    allr = [r for r in dzprobe.load_renders(os.path.join(run, "renders_all.bin")) if r["samples"]]
    rs = [r for r in allr if r["flags"] & 1]
    t0 = rs[0]["timer"]
    first = next(i for i, r in enumerate(rs) if ((r["timer"] - t0) & 0xFFFFFFFF) >= 25 * 44100)
    secs = sum(r["samples"] for r in rs[first:]) / 44100
    c = collections.Counter(); step = -99; n = 0
    for i, r in enumerate(rs):
        if r["ticks"] and r["startedTick"]: step = i
        if i < first: continue
        if 128 - r["space"] - r["totalCyc"] / CPS < below:
            n += 1
            c[("d%d" % min(i - step, 3)) + ("/dbl" if r["samples"] > r["space"] + 3 else "/min")] += 1
    print("%-24s renders left<%g: %4d = %5.2f per 5 s  %s" % (os.path.basename(run.rstrip("/")), below, n, 5 * n / secs,
          " ".join("%s:%d" % kv for kv in sorted(c.items()))))
