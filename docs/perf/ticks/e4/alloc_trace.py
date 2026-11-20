#!/usr/bin/env python3
"""Summarize the E4 allocation trace (dzAllocTrace: GMA calls made inside AudioEngine::routine()).

  alloc_trace.py RUN_DIR ELF            # RUN_DIR/alloc_all.bin (from run_emu_e4.py), groups by call site
  alloc_trace.py FILE.bin ELF --list 50 # also list the first 50 entries in time order

Entry: site (outermost GMA caller's return address), size, result ptr, kind, flags (1 = in song tick), cycles
(PMU, inside the GMA call), audioSampleTimer, two code addresses found on the stack (heuristic callers).
"""
import argparse
import collections
import os
import struct
import subprocess
import sys

KINDS = {1: "alloc", 2: "allocExternal", 3: "allocInternal", 4: "dealloc", 5: "deallocExternal", 6: "extend",
         7: "shorten"}
HDR = 96


def load(path):
    raw = open(path, "rb").read()
    magic, index, esize, n = struct.unpack_from("<IIII", raw)
    assert magic == 0xD2D2A110, "bad magic %#x" % magic
    count_by_kind = struct.unpack_from("<8I", raw, 16)
    cyc_by_kind = struct.unpack_from("<8I", raw, 48)
    in_tick = struct.unpack_from("<I", raw, 80)[0]
    if path.endswith("alloc_all.bin"):
        num = struct.unpack_from("<I", raw, HDR)[0]
        body = raw[HDR + 4:]
        order = range(num)
        get = lambda k: body[k * esize:(k + 1) * esize]
    else:
        lo = max(0, index - n)
        order = range(lo, index)
        get = lambda k: raw[HDR + (k % n) * esize: HDR + (k % n + 1) * esize]
    ents = []
    for k in order:
        site, size, res, kind, flags, _pad, cyc, timer, s0, s1 = struct.unpack("<IIIBBHIIII", get(k))
        ents.append(dict(site=site, size=size, res=res, kind=kind, flags=flags, cyc=cyc, timer=timer, stk=(s0, s1)))
    return dict(index=index, count_by_kind=count_by_kind, cyc_by_kind=cyc_by_kind, in_tick=in_tick, ents=ents)


def addr2line(elf, addrs):
    addrs = sorted(set(a for a in addrs if a))
    if not addrs:
        return {}
    out = subprocess.run(["arm-none-eabi-addr2line", "-e", elf, "-f", "-i", "-C", "-p"] +
                         ["%#x" % (a - 2 if a & 1 == 0 else a - 1) for a in addrs],
                         capture_output=True, text=True, check=True).stdout
    # -p -i: one entry per address, inlined frames on following lines starting with " (inlined by)"
    res, cur = {}, []
    lines = out.splitlines()
    i = 0
    for a in addrs:
        if i >= len(lines):
            break
        chain = [lines[i]]
        i += 1
        while i < len(lines) and lines[i].startswith(" (inlined by)"):
            chain.append(lines[i].strip())
            i += 1
        res[a] = chain
    return res


def short(chain, src_only=True):
    def one(s):
        s = s.replace("(inlined by) ", "")
        fn, _, loc = s.partition(" at ")
        loc = loc.split("/src/deluge/")[-1].split("/src/")[-1]
        fn = fn.split("(")[0].split("<")[0]
        return "%s @ %s" % (fn, loc)
    chain = [c for c in chain if "/toolchain/" not in c] or chain[:1]
    return " <- ".join(one(c) for c in chain)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("elf")
    ap.add_argument("--list", type=int, default=0)
    ap.add_argument("--hz", type=float, default=1e9, help="PMU cycles per second (QEMU 1e9, Deluge 4e8)")
    a = ap.parse_args()
    path = a.path
    if os.path.isdir(path):
        path = os.path.join(path, "alloc_all.bin")
    t = load(path)
    ents = t["ents"]
    print("GMA calls inside AudioEngine::routine(): %d recorded in firmware (%d in song tick); %d entries in file" %
          (t["index"], t["in_tick"], len(ents)))
    for k, name in KINDS.items():
        if t["count_by_kind"][k]:
            print("  %-16s %7d calls, %10d cycles total, %7.0f avg" % (
                name, t["count_by_kind"][k], t["cyc_by_kind"][k], t["cyc_by_kind"][k] / t["count_by_kind"][k]))
    sym = addr2line(a.elf, [e["site"] for e in ents] + [s for e in ents for s in e["stk"]])
    groups = collections.defaultdict(list)
    for e in ents:
        groups[(e["kind"], e["site"], e["stk"][0], e["stk"][1])].append(e)
    print("\nby call site (kind, count, in-tick, sizes, cycles avg/max, first..last audioSampleTimer):")
    for (kind, site, s0, s1), es in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        sizes = collections.Counter(e["size"] for e in es)
        cyc = [e["cyc"] for e in es]
        print("- %s x%d (tick %d) sizes %s cyc avg %.0f max %d  t %d..%d" % (
            KINDS.get(kind, kind), len(es), sum(e["flags"] & 1 for e in es),
            ",".join("%d%s" % (s, "x%d" % c if c > 1 else "") for s, c in sizes.most_common(4)),
            sum(cyc) / len(cyc), max(cyc), es[0]["timer"], es[-1]["timer"]))
        print("    site   %s" % short(sym.get(site, ["%#x" % site])))
        for s in (s0, s1):
            if s:
                print("    stack  %s" % short(sym.get(s, ["%#x" % s])))
    if a.list:
        print("\nfirst %d entries:" % a.list)
        for e in ents[:a.list]:
            print("  t=%9d %-14s size %7d ptr %#010x cyc %7d %s  %s" % (
                e["timer"], KINDS.get(e["kind"], e["kind"]), e["size"], e["res"], e["cyc"],
                "TICK" if e["flags"] & 1 else "    ", short(sym.get(e["site"], ["?"]))[:120]))


if __name__ == "__main__":
    main()
