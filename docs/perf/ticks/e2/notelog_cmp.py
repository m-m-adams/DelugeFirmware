#!/usr/bin/env python3
"""Compare two dzNoteLog dumps (run_emu_notelog.py notelog.bin).
1. Per Sound (name hash): the sequence of voice starts (kind 1: note, velocity) and post-arp note-offs (kind 2: note).
2. Globally: the sequence of all voice starts (sound, note, velocity), and separately of all note-offs.
Timing is ignored. The runs end at different points, so only the common prefix (minus a few events) is compared.
  notelog_cmp.py RUN_A RUN_B"""
import struct, sys, collections

def load(run):
    raw = open(run + "/notelog.bin", "rb").read()
    ev = [struct.unpack_from("<IIhBB", raw, o) for o in range(0, len(raw), 12)]
    return ev

def first_diff(a, b, tail=8):
    n = max(0, min(len(a), len(b)) - tail)
    return n, next((i for i in range(n) if a[i] != b[i]), None)

A, B = load(sys.argv[1]), load(sys.argv[2])
print("A: %d events, %.1f s;  B: %d events, %.1f s" % (len(A), (A[-1][0]-A[0][0])/44100 if A else 0,
                                                      len(B), (B[-1][0]-B[0][0])/44100 if B else 0))
ok = True
ps = collections.defaultdict(lambda: ([], []))
for i, ev in enumerate((A, B)):
    for t, h, note, vel, kind in ev:
        ps[h][i].append((kind, note, vel))
nsame = 0
for h in sorted(ps):
    sa, sb = ps[h]
    n, d = first_diff(sa, sb, tail=4)
    if d is None:
        nsame += 1
    else:
        ok = False
        print("  sound %08x DIFF at %d of %d: A %s B %s" % (h, d, n, sa[d:d+4], sb[d:d+4]))
print("  per Sound: %d of %d sounds identical (%s events compared)" % (nsame, len(ps),
      sum(max(0, min(len(a), len(b)) - 4) for a, b in ps.values())))
for kind, label in ((1, "voice starts"), (2, "note-offs")):
    ga = [(h, n, v) for t, h, n, v, k in A if k == kind]
    gb = [(h, n, v) for t, h, n, v, k in B if k == kind]
    n, d = first_diff(ga, gb, tail=40)
    print("  global order of %s: %d compared, %s" % (label, n, "identical" if d is None else "first difference at %d" % d))
    if d is not None and kind == 1:
        ok = False
print("RESULT:", "IDENTICAL" if ok else "DIFFERENCES")
