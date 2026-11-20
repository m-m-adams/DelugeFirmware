#!/usr/bin/env python3
"""Cull attribution and tick-vs-render characterisation from the tick probe's per-render ring.

  cullattr.py RUN_DIR [RUN_DIR ...] [--skip 25] [--json out.json]

Reads RUN_DIR/renders_all.bin (merged ring over the whole run, from run_emu_rings.py) or, failing that,
RUN_DIR/renders.bin (last 16384 renders only). Only renders while playing, after --skip seconds of audio past the
first playing render.

Units: cycles are QEMU PMU cycles (1 GHz of guest time); 1 sample = 1e9/44100 = 22676 cycles. margin/queued in
samples. tickCost (songTickCost) is reported in samples.

"Step tick" = a tick render whose tick started >= 1 voice (the note-on step). Buckets are renders since the last step
tick render (0 = the step-tick render itself; its culls happen in setDireness() BEFORE its tick, so they can't be
caused by that tick).
"""

import argparse
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe

CPS = 1e9 / 44100  # PMU cycles per sample
F = dzprobe.RENDER_FLAGS
SOFT_THRESH = 48  # kCullMargin: soft cull possible when margin <= 48 - 20 * voicesStartedLastRender
HARD_THRESH = 16  # hard cull when margin <= 16 - 20 * voicesStartedLastRender
BUCKETS = ["0", "1", "2", "3", "4", "5", "6-10", "11+"]


def bucket(d):
    if d is None:
        return None
    if d <= 5:
        return str(d)
    return "6-10" if d <= 10 else "11+"


def pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def load(run):
    for name in ("renders_all.bin", "renders.bin"):
        p = os.path.join(run, name)
        if os.path.exists(p):
            return dzprobe.load_renders(p), name
    raise FileNotFoundError(run)


def analyse(run, skip):
    allr, src = load(run)
    # keep the full list for "previous render" context; select playing renders after skip
    rs = [r for r in allr if r["flags"] & F["playing"] and r["samples"]]
    if not rs:
        return None
    t0 = rs[0]["timer"]
    rs = [r for r in rs if ((r["timer"] - t0) & 0xFFFFFFFF) >= skip * 44100]
    # previous-render started voices (cullVoices' leniency uses voices_started_this_render of the previous render)
    for i, r in enumerate(rs):
        p = rs[i - 1] if i else None
        r["prevStarted"] = (p["startedTick"] + p["startedPending"]) if p else 0
        r["queued"] = 128 - r["space"]
        r["tc"] = r["tickCost"] / 16
        r["pred"] = r["queued"] - r["tc"] - r["margin"]  # predicted render, samples
        r["actual"] = r["renderCyc"] / CPS  # actual render part, samples
        r["started"] = r["startedTick"] + r["startedPending"]
        r["step"] = bool(r["ticks"]) and r["startedTick"] > 0
    last_tick = last_step = None
    for i, r in enumerate(rs):
        if r["ticks"]:
            last_tick = i
        if r["step"]:
            last_step = i
        r["dTick"] = i - last_tick if last_tick is not None else None
        r["dStep"] = i - last_step if last_step is not None else None
    secs = sum(r["samples"] for r in rs) / 44100
    out = dict(
        run=os.path.basename(run.rstrip("/")), src=src, renders=len(rs), secs=secs
    )

    tick = [r for r in rs if r["ticks"]]
    step = [r for r in rs if r["step"]]
    out["ticks_per_s"] = sum(r["ticks"] for r in rs) / secs
    out["step_renders"] = len(step)
    tc = [r["tickCyc"] for r in step]
    tco = [r["tickCyc"] for r in tick if not r["step"]]
    out["stepTickCyc"] = dict(
        mean=st.mean(tc) if tc else 0,
        p50=pct(tc, 50),
        p90=pct(tc, 90),
        p99=pct(tc, 99),
        max=max(tc) if tc else 0,
    )
    out["otherTickCyc"] = dict(
        n=len(tco), mean=st.mean(tco) if tco else 0, max=max(tco) if tco else 0
    )
    noTickCyc = [r["tickCyc"] for r in rs if not r["ticks"]]
    out["noTickFinalizeCyc"] = st.mean(noTickCyc) if noTickCyc else 0
    out["cullsTotal"] = sum(r["culls"] for r in rs)
    out["culls_per_5s"] = 5 * out["cullsTotal"] / secs
    ev = lambda k: sum(1 for r in rs if r["flags"] & F[k])
    out["events"] = dict(
        hard=ev("cullHard"), soft=ev("cullSoft"), underMin=ev("cullUnderMin")
    )
    out["tickCost_mean"] = st.mean(r["tc"] for r in rs)
    out["direness_hist"] = {}
    for r in rs:
        out["direness_hist"][r["direness"]] = (
            out["direness_hist"].get(r["direness"], 0) + 1
        )
    out["direness_gt12_frac"] = sum(1 for r in rs if r["direness"] > 12) / len(rs)
    out["margin"] = dict(
        p1=pct([r["margin"] for r in rs], 1),
        p10=pct([r["margin"] for r in rs], 10),
        p50=pct([r["margin"] for r in rs], 50),
        p90=pct([r["margin"] for r in rs], 90),
    )
    out["queued_p50"] = pct([r["queued"] for r in rs], 50)
    out["samples_mean"] = st.mean(r["samples"] for r in rs)
    out["load"] = sum(r["totalCyc"] for r in rs) / (secs * 1e9)

    # Per bucket (renders since step tick)
    table = {}
    for b in BUCKETS:
        xs = [r for r in rs if bucket(r["dStep"]) == b]
        if not xs:
            continue
        m = lambda k: st.mean(x[k] for x in xs)
        table[b] = dict(
            n=len(xs),
            totalCyc=m("totalCyc"),
            tickCyc=m("tickCyc"),
            renderCyc=m("renderCyc"),
            samples=m("samples"),
            cycPerSample=sum(x["renderCyc"] for x in xs)
            / sum(x["samples"] for x in xs),
            startedTick=m("startedTick"),
            startedPending=m("startedPending"),
            refused=m("refused"),
            margin=m("margin"),
            queued=m("queued"),
            direness=m("direness"),
            voices=m("voices"),
            culls=sum(x["culls"] for x in xs),
            hard=sum(1 for x in xs if x["flags"] & F["cullHard"]),
            soft=sum(1 for x in xs if x["flags"] & F["cullSoft"]),
            undermin=sum(1 for x in xs if x["flags"] & F["cullUnderMin"]),
            cullRenders=sum(1 for x in xs if x["culls"]),
        )
    out["byStep"] = table
    # Same by any tick
    tt = {}
    for b in BUCKETS:
        xs = [r for r in rs if bucket(r["dTick"]) == b]
        if xs:
            tt[b] = dict(
                n=len(xs),
                culls=sum(x["culls"] for x in xs),
                cullRenders=sum(1 for x in xs if x["culls"]),
            )
    out["byTick"] = tt

    # Cull render forensics
    cr = [i for i, r in enumerate(rs) if r["culls"]]
    fx = []
    for i in cr:
        r = rs[i]
        prev = rs[i - 1] if i else None
        # Counterfactual: margin with no songTickCost reservation
        thresh = SOFT_THRESH - 20 * r["prevStarted"]
        fx.append(
            dict(
                dStep=r["dStep"],
                culls=r["culls"],
                hard=bool(r["flags"] & F["cullHard"]),
                margin=r["margin"],
                queued=r["queued"],
                tc=r["tc"],
                pred=r["pred"],
                prevStarted=r["prevStarted"],
                noReserveWouldCull=(r["margin"] + r["tc"]) <= thresh,
                prevTotalSamples=prev["totalCyc"] / CPS if prev else None,
                prevTick=prev["tickCyc"] / CPS if prev else None,
                prevSamples=prev["samples"] if prev else None,
                prevStartedPending=prev["startedPending"] if prev else None,
            )
        )
    out["cullRenders"] = len(cr)
    if fx:
        out["cullFx"] = dict(
            margin_mean=st.mean(f["margin"] for f in fx),
            queued_mean=st.mean(f["queued"] for f in fx),
            tc_mean=st.mean(f["tc"] for f in fx),
            pred_mean=st.mean(f["pred"] for f in fx),
            prevStarted_mean=st.mean(f["prevStarted"] for f in fx),
            noReserveWouldCull_frac=sum(f["noReserveWouldCull"] for f in fx) / len(fx),
            noReserveWouldCull_culls=sum(
                f["culls"] for f in fx if f["noReserveWouldCull"]
            ),
        )
    # How much does a step tick drain the queue? queued at start of step-tick render vs next 1..6 renders
    drain = {k: [] for k in range(8)}
    for i, r in enumerate(rs):
        if r["step"] and i + 7 < len(rs):
            for k in range(8):
                drain[k].append(rs[i + k]["queued"])
    out["queuedAfterStep"] = {k: (st.mean(v) if v else None) for k, v in drain.items()}
    mg = {k: [] for k in range(8)}
    for i, r in enumerate(rs):
        if r["step"] and i + 7 < len(rs):
            for k in range(8):
                mg[k].append(rs[i + k]["margin"])
    out["marginAfterStep"] = {k: (st.mean(v) if v else None) for k, v in mg.items()}
    out["marginAfterStep_p5"] = {k: pct(v, 5) for k, v in mg.items()}
    # prediction error: predicted render vs actual render part (samples) by bucket
    pe = {}
    for b in BUCKETS:
        xs = [r for r in rs if bucket(r["dStep"]) == b and r["flags"] & F["direness"]]
        if xs:
            pe[b] = dict(
                pred=st.mean(x["pred"] for x in xs),
                actual=st.mean(x["actual"] for x in xs),
                tick=st.mean(x["tickCyc"] / CPS for x in xs),
            )
    out["predVsActual"] = pe
    out["_fx"] = fx
    return out


def report(o):
    print(
        "=== %s (%s): %d renders, %.1f s audio, load %.0f%%, %.1f samples/render, queued p50 %d"
        % (
            o["run"],
            o["src"],
            o["renders"],
            o["secs"],
            100 * o["load"],
            o["samples_mean"],
            o["queued_p50"],
        )
    )
    print(
        "ticks/s %.1f; step-tick renders %d; step tick cycles mean %.0f p50 %d p90 %d p99 %d max %d "
        "(= %.1f / %.1f samples mean/max); other ticks n=%d mean %.0f; no-tick finalize %.0f cyc"
        % (
            o["ticks_per_s"],
            o["step_renders"],
            *[o["stepTickCyc"][k] for k in ("mean", "p50", "p90", "p99", "max")],
            o["stepTickCyc"]["mean"] / CPS,
            o["stepTickCyc"]["max"] / CPS,
            o["otherTickCyc"]["n"],
            o["otherTickCyc"]["mean"],
            o["noTickFinalizeCyc"],
        )
    )
    print(
        "culls %d (%.1f /5 s) in %d renders; events hard %d soft %d underMin %d"
        % (o["cullsTotal"], o["culls_per_5s"], o["cullRenders"], *o["events"].values())
    )
    print(
        "songTickCost mean %.1f samples; direness>12 in %.0f%% of renders; direness hist %s"
        % (
            o["tickCost_mean"],
            100 * o["direness_gt12_frac"],
            dict(sorted(o["direness_hist"].items())),
        )
    )
    print(
        "margin p1 %d p10 %d p50 %d p90 %d"
        % tuple(o["margin"][k] for k in ("p1", "p10", "p50", "p90"))
    )
    print(
        "%-6s %6s %8s %8s %8s %6s %7s %5s %5s %5s %6s %6s %5s %6s %6s %4s %4s %4s"
        % (
            "dStep",
            "n",
            "total",
            "tick",
            "render",
            "samp",
            "cyc/smp",
            "sT",
            "sP",
            "ref",
            "margin",
            "queued",
            "dire",
            "voices",
            "culls",
            "hard",
            "soft",
            "min",
        )
    )
    for b, t in o["byStep"].items():
        print(
            "%-6s %6d %8.0f %8.0f %8.0f %6.1f %7.0f %5.2f %5.2f %5.2f %6.1f %6.1f %5.1f %6.1f %6d %4d %4d %4d"
            % (
                b,
                t["n"],
                t["totalCyc"],
                t["tickCyc"],
                t["renderCyc"],
                t["samples"],
                t["cycPerSample"],
                t["startedTick"],
                t["startedPending"],
                t["refused"],
                t["margin"],
                t["queued"],
                t["direness"],
                t["voices"],
                t["culls"],
                t["hard"],
                t["soft"],
                t["undermin"],
            )
        )
    tot = max(1, o["cullsTotal"])
    print(
        "cull share by renders since step tick: "
        + ", ".join(
            "%s:%.0f%%" % (b, 100 * t["culls"] / tot) for b, t in o["byStep"].items()
        )
    )
    print(
        "cull share by renders since any tick:  "
        + ", ".join(
            "%s:%.0f%%" % (b, 100 * t["culls"] / tot) for b, t in o["byTick"].items()
        )
    )
    print(
        "queued after step tick (k=0 is the step render, at its start): "
        + " ".join(
            "%d:%.1f" % (k, v) for k, v in o["queuedAfterStep"].items() if v is not None
        )
    )
    print(
        "margin after step tick mean: "
        + " ".join(
            "%d:%.1f" % (k, v) for k, v in o["marginAfterStep"].items() if v is not None
        )
    )
    print(
        "margin after step tick p5:   "
        + " ".join("%d:%d" % (k, v) for k, v in o["marginAfterStep_p5"].items())
    )
    print(
        "predicted render vs actual render part (samples), tick part: "
        + "; ".join(
            "%s: %.1f/%.1f/%.1f" % (b, v["pred"], v["actual"], v["tick"])
            for b, v in o["predVsActual"].items()
        )
    )
    if "cullFx" in o:
        c = o["cullFx"]
        print(
            "cull renders: margin %.1f queued %.1f songTickCost %.1f predicted %.1f prevStarted %.2f; "
            "would still cross the soft threshold without the songTickCost reservation: %.0f%% of cull renders "
            "(%d culls)"
            % (
                c["margin_mean"],
                c["queued_mean"],
                c["tc_mean"],
                c["pred_mean"],
                c["prevStarted_mean"],
                100 * c["noReserveWouldCull_frac"],
                c["noReserveWouldCull_culls"],
            )
        )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--skip", type=float, default=25.0)
    ap.add_argument("--json")
    ap.add_argument(
        "--list", type=int, default=0, help="print this many cull renders' forensics"
    )
    args = ap.parse_args()
    res = []
    for run in args.runs:
        o = analyse(run, args.skip)
        if o is None:
            print("%s: no playing renders" % run)
            continue
        report(o)
        for f in o["_fx"][: args.list]:
            print("   ", f)
        print()
        res.append(o)
    if args.json:
        json.dump(res, open(args.json, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
