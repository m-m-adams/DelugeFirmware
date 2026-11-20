#!/usr/bin/env python3
"""Compare emulator runs (run_emu.py output directories) with each other and with a hardware probe dump.

  tools/perf/summarize.py runs/cpi145 runs/cache --hw hw.bin --hw-play 136

Local copy of delugemu tools/perf/summarize.py for the tick probe: imports the local dzprobe.py, takes "playing"
windows from the probe's own playing count (the tick test song has no audio clips, so audioVoices is 0), averages
voices over renders (voiceSum), and adds a song-tick table.

Skips the first --skip seconds after PLAY. Emulator time comes from the plugin's modelled cycles (time control) or
from instructions at --ips (icount runs); hardware time from the probe's 100 ms windows.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe


def emu_run(path, skip, ips, warm=5_000_000_000):
    text = open(os.path.join(path, "plugin.log"), errors="replace").read()
    mark = re.search(r"MARK insns=(\d+)", text)
    if not mark:
        raise ValueError("%s: PLAY never happened" % path)
    after = text[mark.start() :]
    slices = [
        dict((k, float(v)) for k, v in re.findall(r"(\w+)=([\d.]+)", l))
        for l in re.findall(r"^SLICE \d+ (.*)$", after, re.MULTILINE)
    ]
    # Full mode prints a slice every 1e8 instructions exactly; lean mode at the first clock update past it
    slices = [s for s in slices if abs(s.get("insns", 0) - 1e8) < 1e6]
    timectl = slices and slices[-1].get("cycles", 0) > 0
    per_slice = (
        (slices[-1]["cycles"] - slices[0]["cycles"]) / 400e6 / (len(slices) - 1)
        if timectl
        else 1e8 / ips
    )
    slices = slices[int(skip / per_slice) :]
    ins = sum(s["insns"] for s in slices)
    rins = sum(s.get("render_ins", 0) for s in slices)
    rfr = sum(s.get("render_frames", 0) for s in slices)
    secs = per_slice * len(slices)
    r = dict(
        name=os.path.basename(path.rstrip("/")),
        frames_s=rfr / secs,
        ins_frame=rins / rfr if rfr else float("nan"),
        l1i=1000 * sum(s["l1i_miss"] for s in slices) / ins,
        l1d=1000 * sum(s["l1d_rd_miss"] + s["l1d_wr_miss"] for s in slices) / ins,
    )
    # Culls, voices and load from the probe ring: windows while playing, after the skip
    w = playing_windows(dzprobe.load_windows(os.path.join(path, "probe.bin")))
    w = w[int(skip * 10) :]
    if not w:
        raise ValueError("%s: no windows while playing after the skip" % path)
    r["w"] = w
    if not rfr:  # lean run: no per-render accounting, so frames from the probe and instructions per frame overall
        r["frames_s"] = sum(x["frames"] for x in w) / (len(w) * 0.1)
        r["ins_frame"] = float("nan")
    r["culls"] = 50 * sum(x["culls"] for x in w) / len(w)
    r["voices"] = voices(w)
    r["load"] = (
        100 * sum(x["sum_cyc"] for x in w) / (len(w) * 0.1 * 1e9)
    )  # QEMU's PMU cycle counter runs at 1 GHz
    # SCOPE totals cover calls from `warm` instructions after PLAY; take frames and renders over the same span
    r["scopes"] = {
        int(i): (int(c), int(n))
        for i, c, n in re.findall(r"SCOPE (\d) calls=(\d+) ins=(\d+)", text)
    }
    start = int(mark[1]) + warm
    spans = [
        int(m[2])
        for m in re.finditer(r"^R (\d+) ins=\d+ fr=(\d+)", text, re.MULTILINE)
        if int(m[1]) >= start and int(m[2])
    ]
    # The plugin's R lines are calls of AudioEngine::routine(), which can render twice; count actual renders
    # (routine_() calls) with the probe's frames-per-render while playing
    fpr = sum(x["frames"] for x in w) / max(1, sum(x["renders"] for x in w))
    r["scope_frames"], r["scope_renders"] = sum(spans), sum(spans) / fpr
    r["fpr"] = fpr
    return r


def hw_run(path, play, skip):
    w = [x for x in dzprobe.load_windows(path) if x["i"] >= play + int(skip * 10)]
    S = lambda k: sum(x.get(k, 0) for x in w)
    fr = S("frames")
    r = dict(
        name="hardware",
        frames_s=fr / (len(w) * 0.1),
        ins_frame=S("sum_ins") / fr,
        l1i=1000 * S("sum_l1i") / S("sum_ins"),
        l1d=1000 * S("sum_l1d") / S("sum_ins"),
        culls=50 * S("culls") / len(w),
        voices=voices(w),
        w=w,
        load=100 * S("sum_cyc") / (len(w) * 0.1 * 400e6),
        fpr=fr / S("renders"),
    )
    r["scopes"] = {
        i: (S("scopeCalls%d" % i), S("scopeIns%d" % i))
        for i in range(6)
        if "scopeCalls%d" % i in w[0]
    }
    r["scope_frames"], r["scope_renders"] = fr, S("renders")
    return r


def playing_windows(ws):
    """Windows while the song was playing: the tick probe counts renders while playing; older probes are taken as
    playing while there are audio clip voices."""
    if ws and "playing" in ws[0]:
        return [x for x in ws if x["playing"]]
    return [x for x in ws if x.get("audioVoices")]


def voices(w):
    if w and "voiceSum" in w[0]:
        return sum(x["voiceSum"] for x in w) / max(1, sum(x["renders"] for x in w))
    return sum(x["voices"] for x in w) / len(w)


def tick_table(rows):
    """Song-tick costs, voice starts and culls from the tick probe's TICK_FIELDS."""
    rows = [r for r in rows if r.get("w") and "ticks" in r["w"][0]]
    if not rows:
        return
    lines = []

    def row(label, f):
        lines.append("  %-34s %s" % (label, " ".join("%16s" % f(r) for r in rows)))

    def S(r, k):
        return sum(x[k] for x in r["w"])

    def secs(r):
        return len(r["w"]) * 0.1

    def per(a, b):
        return a / b if b else float("nan")

    row("", lambda r: r["name"])
    row("ticks/s", lambda r: "%.1f" % (S(r, "ticks") / secs(r)))
    row(
        "renders with a tick %",
        lambda r: "%.1f" % (100 * per(S(r, "tickRenders"), S(r, "renders"))),
    )
    row(
        "tick share of render cycles %",
        lambda r: "%.1f" % (100 * per(S(r, "sum_tickCyc"), S(r, "sum_cyc"))),
    )
    row(
        "tick cycles per tick render (avg)",
        lambda r: "%.0f" % per(S(r, "sum_tickCyc"), S(r, "tickRenders")),
    )
    row(
        "tick cycles, max in a render",
        lambda r: "%d" % max(x["max_tickCyc"] for x in r["w"]),
    )
    row(
        "render cyc/render, tick renders",
        lambda r: "%.0f" % per(S(r, "sum_renderCycTick"), S(r, "tickRenders")),
    )
    row(
        "render cyc/render, other renders",
        lambda r: (
            "%.0f"
            % per(S(r, "sum_renderCycNoTick"), S(r, "renders") - S(r, "tickRenders"))
        ),
    )
    row("voices started in tick /s", lambda r: "%.1f" % (S(r, "startedTick") / secs(r)))
    row(
        "voices started from pending /s",
        lambda r: "%.1f" % (S(r, "startedPending") / secs(r)),
    )
    row(
        "voices started otherwise /s",
        lambda r: "%.1f" % (S(r, "startedOther") / secs(r)),
    )
    row("starts refused in tick /s", lambda r: "%.1f" % (S(r, "refusedTick") / secs(r)))
    row("max pending (estimate)", lambda r: "%d" % max(x["maxPending"] for x in r["w"]))
    row(
        "culls kill/term/force per 5 s",
        lambda r: (
            "%.1f/%.1f/%.1f"
            % tuple(
                50 * S(r, k) / len(r["w"])
                for k in ("cullKill", "cullTerminate", "cullForceRelease")
            )
        ),
    )
    row(
        "cull events hard/soft/min per 5 s",
        lambda r: (
            "%.1f/%.1f/%.1f"
            % tuple(
                50 * S(r, k) / len(r["w"])
                for k in ("cullEventsHard", "cullEventsSoft", "cullEventsUnderMin")
            )
        ),
    )
    lines.append(
        "  culls per 1000 renders, by renders since the last tick render (0 = the tick render itself):"
    )
    for b in dzprobe._BUCKETS:
        row(
            "    %s" % b,
            lambda r, b=b: (
                "%.2f (%.0f%%)"
                % (
                    1000 * per(S(r, "cullsAt" + b), S(r, "rendersAt" + b)),
                    (
                        100
                        * per(
                            S(r, "cullsAt" + b),
                            sum(S(r, "cullsAt" + c) for c in dzprobe._BUCKETS),
                        )
                    ),
                )
            ),
        )
    lines.append("  firmware DzScope, calls/s and inclusive cycles per call:")
    for i, n in enumerate(dzprobe.SCOPE_NAMES):
        row(
            "    " + n[:30],
            lambda r, i=i: (
                "%.0f/s %.0f"
                % (
                    S(r, "scopeCalls%d" % i) / secs(r),
                    per(S(r, "scopeCyc%d" % i), S(r, "scopeCalls%d" % i)),
                )
            ),
        )
    print("\nsong tick (cycles are PMU cycles: QEMU 1 GHz, hardware 400 MHz):")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--hw", help="hardware probe dump (tools/perf/hw_probe.py dump)")
    ap.add_argument(
        "--hw-play",
        type=int,
        help="window index of PLAY in the hardware dump (dzPlayIndex)",
    )
    ap.add_argument(
        "--skip", type=float, default=25.0, help="seconds after PLAY to ignore"
    )
    ap.add_argument(
        "--ips",
        type=float,
        default=5e8,
        help="instructions per second for icount runs (shift 1 = 5e8)",
    )
    args = ap.parse_args()

    rows = []
    if args.hw:
        rows.append(hw_run(args.hw, args.hw_play, args.skip))
    rows += [emu_run(p, args.skip, args.ips) for p in args.runs]
    print(
        "%-12s %9s %8s %6s %9s %7s %7s %7s %7s"
        % (
            "run",
            "frames/s",
            "ins/fr",
            "load%",
            "culls/5s",
            "voices",
            "L1I/kI",
            "L1D/kI",
            "fr/rnd",
        )
    )
    for r in rows:
        print(
            "%-12s %9.0f %8.0f %6.0f %9.1f %7.1f %7.1f %7.1f %7.1f"
            % (
                r["name"],
                r["frames_s"],
                r["ins_frame"],
                r["load"],
                r["culls"],
                r["voices"],
                r["l1i"],
                r["l1d"],
                r["fpr"],
            )
        )
    print("\ninstructions per frame per call, (calls per render):")
    print("  %-38s" % "", " ".join("%16s" % r["name"] for r in rows))
    for i, n in enumerate(dzprobe.SCOPE_NAMES):
        print("  %-38s" % n, " ".join(_scope_cell(r, i) for r in rows))
    tick_table(rows)


def _scope_cell(r, i):
    calls, ins = r["scopes"].get(i, (0, 0))
    if not calls or not r.get("scope_renders"):
        return "%16s" % "-"
    per_render = calls / r["scope_renders"]
    frames_per_render = r["scope_frames"] / r["scope_renders"]
    return "%9.0f (%4.1f)" % (ins / calls / frames_per_render, per_render)


if __name__ == "__main__":
    main()
