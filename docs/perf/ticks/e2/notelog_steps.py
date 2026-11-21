#!/usr/bin/env python3
"""Per-step comparison of two note logs: events grouped by 16th step (from each run's first event, 5512.5 samples
per step at 120 BPM), compared as multisets of (sound, kind, note, velocity). Also reports, for starts that differ in
order, how far apart in time they are.  notelog_steps.py RUN_A RUN_B [--step 5512.5]"""
import struct, sys, collections
STEP = float(sys.argv[sys.argv.index("--step") + 1]) if "--step" in sys.argv else 5512.5
def load(run):
    raw = open(run + "/notelog.bin", "rb").read()
    ev = [struct.unpack_from("<IIhBB", raw, o) for o in range(0, len(raw), 12)]
    t0 = ev[0][0]
    steps = collections.defaultdict(collections.Counter)
    for t, h, n, v, k in ev:
        steps[int((t - t0 + STEP / 2) // STEP)][(h, k, n, v)] += 1
    return steps
a, b = load(sys.argv[1]), load(sys.argv[2])
n = min(max(a), max(b)) - 1
same = sum(1 for s in range(n) if a[s] == b[s])
diff = [s for s in range(n) if a[s] != b[s]]
print("steps compared %d: identical multisets %d, different %d %s" % (n, same, len(diff), diff[:10]))
for s in diff[:3]:
    print("  step %d: only A %s; only B %s" % (s, dict(a[s] - b[s]), dict(b[s] - a[s])))
