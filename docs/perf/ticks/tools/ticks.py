#!/usr/bin/env python3
"""Do culls line up with song ticks? Reads the tick probe's per-render ring (renders.bin, a dump of dzRenders).

  ticks.py runs/base/renders.bin [--list 40]

For each render: was there a tick (and what it cost), how close was the codec to running dry, and were voices culled.
Prints cull counts by renders since the last tick render, how tick renders compare with others, and optionally the
renders around the first --list culls. Cycles are PMU cycles (QEMU: 1 GHz of guest time).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("renders")
    ap.add_argument(
        "--list", type=int, default=0, help="show the renders around this many culls"
    )
    ap.add_argument(
        "--context", type=int, default=4, help="renders before/after each listed cull"
    )
    args = ap.parse_args()

    rs = [
        r
        for r in dzprobe.load_renders(args.renders)
        if r["flags"] & dzprobe.RENDER_FLAGS["playing"]
    ]
    if not rs:
        sys.exit("no renders while playing")
    secs = sum(r["samples"] for r in rs) / 44100
    tick = [r for r in rs if r["ticks"]]
    other = [r for r in rs if not r["ticks"]]
    avg = lambda xs, k: sum(x[k] for x in xs) / len(xs) if xs else float("nan")
    print(
        "%d renders while playing (%.1f s of audio), %d with a tick"
        % (len(rs), secs, len(tick))
    )
    print("%-24s %10s %10s" % ("", "tick", "no tick"))
    for k in (
        "tickCyc",
        "renderCyc",
        "totalCyc",
        "samples",
        "space",
        "startedTick",
        "startedPending",
        "culls",
        "voices",
        "margin",
    ):
        print("%-24s %10.1f %10.1f" % (k, avg(tick, k), avg(other, k)))

    # Renders since the last tick render (0: the tick render itself, whose culls come before its tick)
    since = []
    last = None
    for i, r in enumerate(rs):
        if r["ticks"]:
            last = i
            since.append(0)
        else:
            since.append(i - last if last is not None else 10**6)
    hist = {}
    for d, r in zip(since, rs):
        h = hist.setdefault(min(d, 9), [0, 0, 0])
        h[0] += 1
        h[1] += r["culls"]
        h[2] += bool(r["culls"])
    total = sum(h[1] for h in hist.values())
    print(
        "\nrenders since tick  renders   culls  renders-with-cull  culls/1000 renders  share of culls"
    )
    for d in sorted(hist):
        n, c, rc = hist[d]
        print(
            "%17s %9d %7d %18d %19.2f %14.0f%%"
            % (
                str(d) if d < 9 else "9+",
                n,
                c,
                rc,
                1000 * c / n,
                100 * c / total if total else 0,
            )
        )

    if args.list:
        shown = 0
        print(
            "\n%8s %4s %8s %8s %8s %5s %5s %3s %3s %3s %5s %4s %5s"
            % (
                "render",
                "d",
                "tickCyc",
                "rendCyc",
                "total",
                "space",
                "samp",
                "sT",
                "sP",
                "cul",
                "voice",
                "dire",
                "marg",
            )
        )
        for i, r in enumerate(rs):
            if not r["culls"]:
                continue
            for j in range(
                max(0, i - args.context), min(len(rs), i + args.context + 1)
            ):
                x = rs[j]
                print(
                    "%8d %4s %8d %8d %8d %5d %5d %3d %3d %3d %5d %4d %5d%s"
                    % (
                        x["n"],
                        since[j] if since[j] < 10**6 else "-",
                        x["tickCyc"],
                        x["renderCyc"],
                        x["totalCyc"],
                        x["space"],
                        x["samples"],
                        x["startedTick"],
                        x["startedPending"],
                        x["culls"],
                        x["voices"],
                        x["direness"],
                        x["margin"],
                        "  <" if j == i else "",
                    )
                )
            print()
            shown += 1
            if shown >= args.list:
                break


if __name__ == "__main__":
    main()
