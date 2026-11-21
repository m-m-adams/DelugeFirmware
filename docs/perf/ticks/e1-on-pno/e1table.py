#!/usr/bin/env python3
"""Markdown rows for the E1-on-PNO results table: per song and build, runs aggregated (culls listed per run, the rest
mean or min). Uses tb_report.py's and e1report.py's measures.  e1table.py LABEL=RUN[,RUN...] ..."""
import os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe
import tb_report  # noqa
CPS = 1e9 / 44100
F = dzprobe.RENDER_FLAGS

def one(run):
    rs = [r for r in dzprobe.load_renders(os.path.join(run, "renders_all.bin")) if r["flags"] & 1 and r["samples"]]
    t0 = rs[0]["timer"]
    first = next(i for i, r in enumerate(rs) if ((r["timer"] - t0) & 0xFFFFFFFF) >= 25 * 44100)
    sel = rs[first:]
    secs = sum(r["samples"] for r in sel) / 44100
    el = sum(r["dt"] for r in sel[1:] if r["dt"] is not None)
    left = [128 - r["space"] - r["totalCyc"] / CPS for r in sel]
    hist = collections.Counter(r["direness"] for r in sel)
    nd = tb_report.note_delays(rs)
    return dict(culls=5 * sum(r["culls"] for r in sel) / secs,
                hard=5 * sum(1 for r in sel if r["flags"] & F["cullHard"]) / secs,
                soft=5 * sum(1 for r in sel if r["flags"] & (F["cullSoft"] | F["cullUnderMin"])) / secs,
                voices=sum(r["voices"] for r in sel) / len(sel), load=sum(r["totalCyc"] for r in sel[:-1]) / el,
                minq=min(128 - r["space"] for r in sel), left=min(left), neg=sum(1 for x in left if x < 0),
                lt4=sum(1 for x in left if x < 4), dire=sum(d * c for d, c in hist.items()) / len(sel),
                d14=100 * hist.get(14, 0) / len(sel), nd=nd)

for arg in sys.argv[1:]:
    label, runs = arg.split("=", 1)
    res = [one(r) for r in runs.split(",")]
    m = lambda k: sum(x[k] for x in res) / len(res)
    nds = [x["nd"] for x in res]
    nm = lambda k: sum(x[k] for x in nds if x) / max(1, sum(1 for x in nds if x))
    print("| %s | %s (%.1f/%.1f) | %.1f | %.0f%% | %d | %.1f | %d / %d | %.1f (14: %.0f%%) | %.1f / %.1f | %.1f / %.1f |" % (
        label, " / ".join("%.1f" % x["culls"] for x in res), m("hard"), m("soft"), m("voices"), 100 * m("load"),
        min(x["minq"] for x in res), min(x["left"] for x in res), sum(x["neg"] for x in res), sum(x["lt4"] for x in res),
        m("dire"), m("d14"), nm("mean"), nm("p90"), nm("chord_mean"), max(x["chord_max"] for x in nds if x)))
