#!/usr/bin/env python3
"""Compare two dzNoteLog dumps (run_emu_notelog.py notelog.bin): per Sound (name hash), the sequence of
voice starts (kind 1: note, velocity) and post-arp note-offs (kind 2: note). Timing is ignored (it may move by a
render); order per Sound and content must match.
  notelog_cmp.py RUN_A RUN_B [--skip-events N]"""
import struct, sys, collections

def load(run):
    raw = open(run + "/notelog.bin", "rb").read()
    out = collections.OrderedDict()
    t = []
    for o in range(0, len(raw), 12):
        timer, h, note, vel, kind = struct.unpack_from("<IIhBB", raw, o)
        out.setdefault(h, []).append((kind, note, vel))
        t.append(timer)
    return out, t

a, ta = load(sys.argv[1]); b, tb = load(sys.argv[2])
print("A: %d events, %d sounds, %.1f s;  B: %d events, %d sounds, %.1f s" % (
    sum(map(len, a.values())), len(a), (ta[-1]-ta[0])/44100 if ta else 0,
    sum(map(len, b.values())), len(b), (tb[-1]-tb[0])/44100 if tb else 0))
ok = True
for h in sorted(set(a) | set(b)):
    sa, sb = a.get(h, []), b.get(h, [])
    n = min(len(sa), len(sb))
    # drop the last few events: the runs end at different points
    n = max(0, n - 8)
    mism = next((i for i in range(n) if sa[i] != sb[i]), None)
    ons = sum(1 for e in sa[:n] if e[0] == 1)
    if mism is None and n > 0:
        status = "same"
    else:
        ok = False
        status = "DIFF at %s: A %s B %s" % (mism, sa[mism:mism+4] if mism is not None else None,
                                          sb[mism:mism+4] if mism is not None else None)
    print("  sound %08x: compared %d events (%d starts, %d distinct notes): %s" % (
        h, n, ons, len({e[1] for e in sa[:n] if e[0] == 1}), status))
print("RESULT:", "IDENTICAL note sequences" if ok else "DIFFERENCES")
