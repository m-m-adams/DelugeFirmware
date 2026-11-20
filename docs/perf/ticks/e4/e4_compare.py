#!/usr/bin/env python3
"""E4: compare runs (renders_all.bin from run_emu_rings.py / run_emu_e4.py) - steady state, song start, and the
renders around the E4FX song's half-bar automation toggles (where the base firmware allocates/frees FX buffers).

  e4_compare.py RUN_DIR [RUN_DIR ...] [--start 2.0] [--period 44100] [--win 2000] [--secs 20]

Cycles are QEMU PMU cycles = ns of guest time (1 sample = 22,676 ns). "us/smp" = render+tick time per output sample.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe  # noqa: E402


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p / 100 * len(v)))] if v else float("nan")


def stats(rs):
    if not rs:
        return dict(n=0)
    smp = sum(r["samples"] for r in rs)
    tot = [r["totalCyc"] for r in rs]
    return dict(n=len(rs), secs=smp / 44100, load=100 * sum(tot) / (smp * 22676.0),
                mean=sum(tot) / len(rs), p99=pct(tot, 99), max=max(tot),
                culls5=5 * sum(r["culls"] for r in rs) / (smp / 44100), culls=sum(r["culls"] for r in rs),
                minspace_q=min(128 - r["space"] for r in rs),
                voices=sum(r["voices"] for r in rs) / len(rs))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--start", type=float, default=2.0, help="seconds after PLAY counted as 'song start'")
    ap.add_argument("--period", type=int, default=44100, help="automation toggle period in samples (half bar)")
    ap.add_argument("--win", type=int, default=2000, help="samples after each toggle counted as 'toggle window'")
    ap.add_argument("--secs", type=float, default=20.0, help="only use this many seconds after PLAY")
    a = ap.parse_args()
    rows = []
    for d in a.runs:
        p = os.path.join(d, "renders_all.bin")
        if not os.path.exists(p):
            p = os.path.join(d, "renders.bin")
        rs = [r for r in dzprobe.load_renders(p) if r["flags"] & 1]
        t0 = rs[0]["timer"]
        rs = [r for r in rs if (r["timer"] - t0) < a.secs * 44100]
        start = [r for r in rs if r["timer"] - t0 < a.start * 44100]
        steady = [r for r in rs if r["timer"] - t0 >= a.start * 44100]
        tog = [r for r in steady if (r["timer"] - t0) % a.period < a.win]
        notog = [r for r in steady if (r["timer"] - t0) % a.period >= a.win]
        rows.append((os.path.basename(d.rstrip("/")), stats(start), stats(steady), stats(tog), stats(notog)))
        st = os.path.join(d, "deferredAllocStats.bin")
        if os.path.exists(st):
            names = ["magic", "requestsQueued", "requestsServed", "requestsFailed", "requestsCancelled",
                     "requestsTableFull", "requestsResized", "immediateAllocs", "freesQueued", "freesServed",
                     "freesQueueFull", "serviceRuns", "maxQueued", "syncAllocsInAudio", "syncDeallocsInAudio",
                     "lastSyncAllocCaller", "lastSyncDeallocCaller", "maxLatencySamples", "sumLatencySamples"]
            raw = open(st, "rb").read()
            names = names[:len(raw) // 4]
            v = struct.unpack_from("<%dI" % len(names), raw)
            print("%s deferredAllocStats: %s" % (rows[-1][0], ", ".join(
                "%s=%s" % (k, ("%#x" % x) if "Caller" in k or k == "magic" else x) for k, x in zip(names, v)
                )))
    hdr = ["part", "metric"] + [r[0] for r in rows]
    print("\n| " + " | ".join(hdr) + " |\n|" + "---|" * len(hdr))
    for i, part in enumerate(["start (first %gs)" % a.start, "steady", "toggle windows", "other steady"], 1):
        for m, fmt in [("secs", "%.1f"), ("load", "%.1f"), ("mean", "%.0f"), ("p99", "%.0f"), ("max", "%.0f"),
                       ("culls5", "%.2f"), ("culls", "%d"), ("minspace_q", "%d"), ("voices", "%.1f")]:
            vals = [(fmt % r[i][m]) if r[i].get("n") else "-" for r in rows]
            print("| %s | %s | %s |" % (part, m, " | ".join(vals)))


if __name__ == "__main__":
    main()
