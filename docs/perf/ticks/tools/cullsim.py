#!/usr/bin/env python3
"""Replay cullVoices()' decision on the per-render ring under alternative margin definitions (first-order what-if).

  cullsim.py RUN_DIR [RUN_DIR ...] [--skip 25]

Static replay: each render's recorded queue/prediction is reused; the counterfactual's effect on later renders
(fewer culls -> more voices -> heavier renders) is NOT modelled. Policy "asis" re-derives the firmware's decision and
should match the recorded cull flags (validation).

cullVoices() (audio_engine.cpp): scaled = 128 - margin; only called when scaled >= direnessThreshold (50);
over = scaled - (80 + 20 * voices_started_this_render), where voices_started_this_render is still the PREVIOUS
render's count when setDireness() runs; voices > MIN_VOICES (7): over >= 32 -> hard; over >= 0 and last_over > 0 and
over >= last_over -> soft (last_over is only updated when cullVoices runs). voices <= 7: over >= 40 -> under-min.

Policies (margin used in place of the recorded one):
  asis      recorded margin = queued - songTickCost - predict(getRenderSize(space))
  noTC      + songTickCost (no tick reservation at all)
  tickOnly  songTickCost only in renders that actioned a tick (E1: reserve only when a tick is due this window)
  minSize   predicted render scaled to the minimum render (space samples) instead of the doubled size
            (pred - perSample * (rendered - space), perSample from a linear fit of render cycles on samples)
  tick+min  tickOnly and minSize together
Also prints the truth: the least queued (codec buffer) seen at any render start, i.e. how close audio came to an
underrun, and the chord start latency (samples from a step tick to the last of its notes starting).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe

F = dzprobe.RENDER_FLAGS
CPS = 1e9 / 44100


def load(run):
    for name in ("renders_all.bin", "renders.bin"):
        p = os.path.join(run, name)
        if os.path.exists(p):
            return dzprobe.load_renders(p)
    raise FileNotFoundError(run)


def simulate(rs, margins):
    last_over = 0
    out = []
    for i, r in enumerate(rs):
        m = margins[i]
        scaled = max(128 - m, 0)
        kind = None
        if scaled >= 50:
            prev = rs[i - 1] if i else None
            started = (prev["startedTick"] + prev["startedPending"]) if prev else 0
            voices = prev["voices"] if prev else r["voices"]
            over = scaled - (80 + 20 * started)
            if voices > 7:
                if over >= 32:
                    kind = "hard"
                elif over >= 0 and last_over > 0 and over >= last_over:
                    kind = "soft"
            elif over >= 40:
                kind = "min"
            last_over = over
        out.append(kind)
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--skip", type=float, default=25.0)
    args = ap.parse_args()
    for run in args.runs:
        allr = load(run)
        rs = [r for r in allr if r["flags"] & F["playing"] and r["samples"]]
        t0 = rs[0]["timer"]
        first = next(
            i
            for i, r in enumerate(rs)
            if ((r["timer"] - t0) & 0xFFFFFFFF) >= args.skip * 44100
        )
        secs = sum(r["samples"] for r in rs[first:]) / 44100
        q = [128 - r["space"] for r in rs]
        tc = [r["tickCost"] / 16 for r in rs]
        m0 = [r["margin"] for r in rs]
        pred = [q[i] - tc[i] - m0[i] for i in range(len(rs))]
        # render size used in setDireness() isn't recorded; the rendered size is (tick truncation can make it smaller)
        print(
            "=== %s: %.1f s after skip, %d renders"
            % (os.path.basename(run.rstrip("/")), secs, len(rs) - first)
        )
        # per-sample render cost (samples of time per sample rendered), least-squares over renders without a tick
        fit = [r for r in rs[first:] if not r["ticks"] and r["samples"] >= 4]
        n_, sx = len(fit), sum(r["samples"] for r in fit)
        sy = sum(r["renderCyc"] / CPS for r in fit)
        sxx, sxy = (
            sum(r["samples"] ** 2 for r in fit),
            sum(r["samples"] * r["renderCyc"] / CPS for r in fit),
        )
        per = (n_ * sxy - sx * sy) / (n_ * sxx - sx * sx)
        fixed = (sy - per * sx) / n_
        print(
            "  render cost fit (no-tick renders): %.1f samples fixed + %.3f per sample"
            % (fixed, per)
        )
        # predicted render for the minimum size (space) instead of the rendered (doubled) size
        pmin = [
            max(0.0, pred[i] - per * max(0, rs[i]["samples"] - rs[i]["space"]))
            for i in range(len(rs))
        ]
        pol = {
            "asis": m0,
            "noTC": [m0[i] + tc[i] for i in range(len(rs))],
            "tickOnly": [
                m0[i] + (0 if rs[i]["ticks"] else tc[i]) for i in range(len(rs))
            ],
            "minSize": [q[i] - tc[i] - pmin[i] for i in range(len(rs))],
            "tick+min": [
                q[i] - (tc[i] if rs[i]["ticks"] else 0) - pmin[i]
                for i in range(len(rs))
            ],
        }
        actual = [
            "hard"
            if r["flags"] & F["cullHard"]
            else "soft"
            if r["flags"] & F["cullSoft"]
            else "min"
            if r["flags"] & F["cullUnderMin"]
            else None
            for r in rs
        ]
        a = actual[first:]
        print(
            "  recorded cull events: hard %d soft %d min %d  (culled voices %d)"
            % (
                a.count("hard"),
                a.count("soft"),
                a.count("min"),
                sum(r["culls"] for r in rs[first:]),
            )
        )
        for name, ms in pol.items():
            s = simulate(rs, ms)[first:]
            agree = sum(1 for x, y in zip(s, a) if x == y) / len(a)
            print(
                "  %-9s hard %4d soft %4d min %4d  -> %5.1f events/5 s%s"
                % (
                    name,
                    s.count("hard"),
                    s.count("soft"),
                    s.count("min"),
                    5 * (len(s) - s.count(None)) / secs,
                    "   (agreement with recorded %.4f)" % agree
                    if name == "asis"
                    else "",
                )
            )
        qq = q[first:]
        print(
            "  truth: least queued at a render start %d samples (p0.1 %d, p1 %d); renders starting with < 16 queued: %d"
            % (
                min(qq),
                sorted(qq)[len(qq) // 1000],
                sorted(qq)[len(qq) // 100],
                sum(1 for x in qq if x < 16),
            )
        )
        # least "time left" after a render: queued at start minus the render's actual total time (tick + render)
        left = [q[i] - rs[i]["totalCyc"] / (1e9 / 44100) for i in range(first, len(rs))]
        print(
            "  actual time left in the codec buffer when a render finished: min %.1f p0.1 %.1f p1 %.1f samples"
            % (
                min(left),
                sorted(left)[len(left) // 1000],
                sorted(left)[len(left) // 100],
            )
        )
        # chord start latency
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
            got = want = (
                1 if k < len(rs) and rs[k]["pending"] <= base and k - i < 400 else 0
            )
            want = 1
            if got >= want:
                lat.append((smp, k - i))
        if lat:
            ls = sorted(x[0] for x in lat)
            print(
                "  chord start latency (step-tick render start -> start of the render that starts the step's last pending note): mean %.0f samples (%.1f ms), "
                "p50 %d, max %d samples; renders mean %.1f"
                % (
                    sum(ls) / len(ls),
                    1000 * sum(ls) / len(ls) / 44100,
                    ls[len(ls) // 2],
                    ls[-1],
                    sum(x[1] for x in lat) / len(lat),
                )
            )


if __name__ == "__main__":
    main()
