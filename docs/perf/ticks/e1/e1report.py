#!/usr/bin/env python3
"""One line per run for the E1 results table, from renders_all.bin (after --skip seconds of playback).

  e1report.py RUN_DIR [RUN_DIR ...] [--skip 25]

culls/5s = voices culled; ev h/s = cull events hard/soft per 5 s; load = sum of routine_() cycles / elapsed cycles;
size = rendered samples per render mean (p10/p50/p90); minQ = least queued at a render start (and how many renders
started with < 16 queued); left = least (queued - render's total time) when a render finished; dire = direness
histogram; lat = chord start latency (same algorithm as cullsim.py), ms mean / renders.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe  # noqa: E402

F = dzprobe.RENDER_FLAGS
CPS = 1e9 / 44100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--skip", type=float, default=25.0)
    args = ap.parse_args()
    print("%-22s %5s %8s %9s %6s %5s %-17s %8s %6s %-28s %s" % (
        "run", "secs", "culls/5s", "ev h/s", "voices", "load", "size mean(p10/50/90)", "minQ(<16)", "left",
        "direness", "chord lat"))
    for run in args.runs:
        for name in ("renders_all.bin", "renders.bin"):
            p = os.path.join(run, name)
            if os.path.exists(p):
                allr = dzprobe.load_renders(p)
                break
        rs = [r for r in allr if r["flags"] & F["playing"] and r["samples"]]
        t0 = rs[0]["timer"]
        first = next(i for i, r in enumerate(rs) if ((r["timer"] - t0) & 0xFFFFFFFF) >= args.skip * 44100)
        sel = rs[first:]
        secs = sum(r["samples"] for r in sel) / 44100
        culls = sum(r["culls"] for r in sel)
        hard = sum(1 for r in sel if r["flags"] & F["cullHard"])
        soft = sum(1 for r in sel if r["flags"] & (F["cullSoft"] | F["cullUnderMin"]))
        voices = sum(r["voices"] for r in sel) / len(sel)
        el = (sel[-1]["cycStart"] - sel[0]["cycStart"]) & 0xFFFFFFFF
        el += sel[-1]["totalCyc"]
        # cycStart wraps every 4.3 s of guest time: sum the dt instead
        el = sum(rs[i]["dt"] for i in range(first + 1, len(rs)) if rs[i]["dt"] is not None)
        load = sum(r["totalCyc"] for r in sel[:-1]) / el
        sz = sorted(r["samples"] for r in sel)
        q = [128 - r["space"] for r in sel]
        left = min(128 - r["space"] - r["totalCyc"] / CPS for r in sel)
        hist = {}
        for r in sel:
            hist[r["direness"]] = hist.get(r["direness"], 0) + 1
        dire = " ".join("%d:%.0f%%" % (d, 100 * c / len(sel)) for d, c in sorted(hist.items()) if c / len(sel) >= 0.01)
        lat = []
        for i in range(first, len(rs)):
            r = rs[i]
            if not (r["ticks"] and r["startedTick"]):
                continue
            base = rs[i - 1]["pending"]
            smp, k = 0, i
            while k < len(rs) and rs[k]["pending"] > base and k - i < 400:
                smp += rs[k]["samples"]
                k += 1
            if k < len(rs) and rs[k]["pending"] <= base and k - i < 400:
                lat.append((smp, k - i))
        lats = ("%.1f ms / %.1f r" % (1000 * sum(x[0] for x in lat) / len(lat) / 44100,
                                      sum(x[1] for x in lat) / len(lat))) if lat else "-"
        print("%-22s %5.1f %8.1f %4.1f/%-4.1f %6.1f %4.0f%% %5.1f (%3d/%3d/%3d) %4d(%d) %6.1f %-28s %s" % (
            os.path.basename(run.rstrip("/")), secs, 5 * culls / secs, 5 * hard / secs, 5 * soft / secs, voices,
            100 * load, sum(sz) / len(sz), sz[len(sz) // 10], sz[len(sz) // 2], sz[9 * len(sz) // 10], min(q),
            sum(1 for x in q if x < 16), left, dire, lats))


if __name__ == "__main__":
    main()
