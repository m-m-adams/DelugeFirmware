#!/usr/bin/env python3
"""E1 replay: re-derive setDireness()/getRenderSize() on the per-render ring under the E1 rules (static what-if).

  e1sim.py RUN_DIR [RUN_DIR ...] [--skip 25]

Unlike cullsim.py this rebuilds the firmware's RenderCost fit (decayed linear fit of render time on render size,
updated after every render from the ring's samples / renderCyc) and songTickCost (from the ring), so it can
recompute getRenderSize() under each rule - E1 (a) changes getRenderSize()'s cap too, which cullsim's "tickOnly"
doesn't model. Static: each render's recorded queue, size and cost are reused; extra voices left playing are not.

Rules (cumulative, like the E1 commits):
  asis   firmware 680b93c: margin = queued - songTickCost - predict(getRenderSize(space))       (validation)
  a      songTickCost only when a tick is due at the window start (in getRenderSize()'s cap too)
  a+b    + predict min(getRenderSize, timeTilNextTick) when a tick cuts the window short
  a+b+c  + judge on the smallest render: min(space, timeTilNextTick)
  ab+sN  a+b, with getRenderSize() doubling only while it leaves N samples (kRenderSafetyMargin = N), judged on
         the render getRenderSize() picks (as in a+b)
Prints cull events/5 s (hard/soft/min) and the direness histogram each rule would give (direness dynamics replayed:
rises at once, falls by 1 per 1/8 s).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dzprobe  # noqa: E402

F = dzprobe.RENDER_FLAGS
CPS = 1e9 / 44100


class RenderCost:
    def __init__(self):
        self.count = self.size = self.time = self.ss = self.st = 0.0

    def add(self, n, t):
        d = 15 / 16
        self.count = self.count * d + 1
        self.size = self.size * d + n
        self.time = self.time * d + t
        self.ss = self.ss * d + n * n
        self.st = self.st * d + n * t

    def _line(self):
        spread = self.count * self.ss - self.size * self.size
        if spread > self.count * self.count * 64:
            per = (self.count * self.st - self.size * self.time) / spread
            fixed = (self.time - per * self.size) / self.count
            if per > 0 and fixed >= 0:
                return fixed, per
        return None

    def predict(self, n):
        if self.size == 0:
            return n
        line = self._line()
        if line:
            return line[0] + line[1] * n
        return self.time / self.size * n

    def size_for(self, t):
        if self.size == 0 or self.time <= 0:
            return t
        line = self._line()
        if line:
            return (t - line[0]) / line[1]
        return t * self.size / self.time


def render_size(space, rc, reserve, safety=8):
    n = space
    if n < 128:
        over = n - 6
        if over > 0:
            n = 6 + 2 * over
    n = min(n, 128)
    if n > space:
        avail = 128 - space - safety - reserve
        allowed = rc.size_for(avail)
        if allowed < n:
            n = max(space, int(max(allowed, 0.0)))
    if n >= 3:
        n = (n + 2) & ~3
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--skip", type=float, default=25.0)
    args = ap.parse_args()
    rules = ["asis", "a", "a+b", "a+b+c", "ab+s24", "ab+s48", "ab+s56", "abs+la"]
    for run in args.runs:
        for name in ("renders_all.bin", "renders.bin"):
            p = os.path.join(run, name)
            if os.path.exists(p):
                allr = dzprobe.load_renders(p)
                break
        rs = [r for r in allr if r["samples"]]
        play = [i for i, r in enumerate(rs) if r["flags"] & F["playing"]]
        t0 = rs[play[0]]["timer"]
        first = next(i for i in play if ((rs[i]["timer"] - t0) & 0xFFFFFFFF) >= args.skip * 44100)
        rc = RenderCost()
        margins = {k: [None] * len(rs) for k in rules}
        sizes = {k: [None] * len(rs) for k in rules}
        for i, r in enumerate(rs):
            q = 128 - r["space"]
            tc = r["tickCost"] / 16
            tick = r["ticks"] > 0
            nxt_tick = i + 1 < len(rs) and rs[i + 1]["ticks"] > 0
            # truncated at the next tick: the window ended exactly at it
            til = r["samples"] if (not tick and nxt_tick) else 9999
            g_full = render_size(r["space"], rc, tc)
            g_a = render_size(r["space"], rc, tc if tick else 0)
            res = tc if tick else 0
            # abs+la: ab+s56, and also the margin the render after this one would have if it were just its space
            n0 = min(render_size(r["space"], rc, res, 56), til)
            m_now = q - int(res + rc.predict(n0))
            q1 = q - (res + rc.predict(n0)) + n0
            s1 = max(1, min(128, int(128 - q1)))
            res1 = tc if (not tick and til == n0) else 0
            m_next = q1 - int(res1 + rc.predict(s1))
            la_margin = int(min(m_now, m_next))
            for k, (n, reserve) in {
                "asis": (g_full, tc),
                "a": (g_a, res),
                "a+b": (min(g_a, til), res),
                "a+b+c": (min(r["space"], til), res),
                "ab+s24": (min(render_size(r["space"], rc, res, 24), til), res),
                "ab+s48": (min(render_size(r["space"], rc, res, 48), til), res),
                "ab+s56": (min(render_size(r["space"], rc, res, 56), til), res),
            }.items():
                sizes[k][i] = n
                margins[k][i] = q - int(reserve + rc.predict(n))
            sizes["abs+la"][i] = n0
            margins["abs+la"][i] = la_margin
            rc.add(r["samples"], r["renderCyc"] / CPS)
        sel = range(first, len(rs))
        play_sel = [i for i in sel if rs[i]["flags"] & F["playing"]]
        secs = sum(rs[i]["samples"] for i in play_sel) / 44100
        diffs = [abs(margins["asis"][i] - rs[i]["margin"]) for i in play_sel]
        print("=== %s: %.1f s, %d renders; asis margin vs recorded: mean |d| %.2f, within 2: %.1f%%" % (
            os.path.basename(run.rstrip("/")), secs, len(play_sel), sum(diffs) / len(diffs),
            100 * sum(1 for d in diffs if d <= 2) / len(diffs)))
        actual = sum(1 for i in play_sel if rs[i]["flags"] & (F["cullHard"] | F["cullSoft"] | F["cullUnderMin"]))
        print("  recorded cull events: %.1f /5 s" % (5 * actual / secs))
        for k in rules:
            last_over, dire, tchg = 0, 0, 0
            kinds, hist = [], {}
            for i, r in enumerate(rs):
                m = margins[k][i]
                scaled = max(80 + 48 - m, 0)
                kind = None
                if scaled >= 50:
                    nd = min(scaled - 49, 14)
                    if nd >= dire:
                        dire, tchg = nd, r["timer"]
                    prev = rs[i - 1] if i else r
                    started = prev["startedTick"] + prev["startedPending"] if i else 0
                    voices = prev["voices"]
                    over = scaled - (80 + 20 * started)
                    if voices > 7:
                        if over >= 32:
                            kind = "hard"
                        elif over >= 0 and last_over > 0 and over >= last_over:
                            kind = "soft"
                    elif over >= 40:
                        kind = "min"
                    last_over = over
                elif scaled < 47 and ((r["timer"] - tchg) & 0xFFFFFFFF) >= 5512:
                    tchg = r["timer"]
                    dire = max(dire - 1, 0)
                if i >= first and r["flags"] & F["playing"]:
                    kinds.append(kind)
                    hist[dire] = hist.get(dire, 0) + 1
            ms = sorted(margins[k][i] for i in play_sel)
            n = len(kinds)
            print("  %-6s hard %4d soft %4d min %3d -> %6.1f events/5 s | margin p1 %4d p50 %4d | direness %s" % (
                k, kinds.count("hard"), kinds.count("soft"), kinds.count("min"),
                5 * (n - kinds.count(None)) / secs, ms[len(ms) // 100], ms[len(ms) // 2],
                " ".join("%d:%.0f%%" % (d, 100 * c / n) for d, c in sorted(hist.items()) if c / n >= 0.005)))


if __name__ == "__main__":
    main()
