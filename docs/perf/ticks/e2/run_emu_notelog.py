#!/usr/bin/env python3
"""Run the dz probe firmware in the emulator under the dzcache plugin and collect its reports.

The probe firmware loads and plays its song by itself, so no input is needed. Host audio is never connected.
Writes <out>/plugin.log (SLICE/MARK/R/SCOPE lines), <out>/probe.bin (the probe's window ring), <out>/renders.bin
(the tick probe's per-render ring, dzRenders) and <out>/run.log.

run_emu_rings.py (baseline agent): run_emu.py + the per-render ring is dumped every poll (VM stopped for the dump,
harmless under time control) to <out>/rings/rNNNN.bin and merged into <out>/renders_all.bin (same format, one entry
per render over the whole run; load with dzprobe.load_renders). The 16384-entry ring alone holds only ~8 s.

Local copy of delugemu tools/perf/run_emu.py for the tick probe: uses the local dzprobe.py (tick-path scopes s0..s5)
and also dumps dzRenders. The emulator checkout is $DELUGEMU (default /home/user/synthstromaudible/delugemu).

  tools/perf/run_emu.py --elf probe.elf --out runs/cpi145 --plugin timectl=1,cachesim=0,cpi=1.45 --lean
  tools/perf/run_emu.py --elf probe.elf --out runs/ic1 --icount 1 --plugin cachesim=0
"""
import argparse
import os
import sys
import time

HERE = "/home/user/perfwork/tools"
REPO = os.environ.get("DELUGEMU", "/home/user/synthstromaudible/delugemu")
sys.path.insert(0, os.path.join(REPO, "pylib"))
sys.path.insert(0, HERE)
from delugemu import DelugeSession  # noqa: E402
import dzprobe  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elf", required=True, help="probe firmware ELF")
    ap.add_argument("--sd", default=os.path.join(REPO, "build", "deluge_sd.img"))
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--plugin", default="cachesim=0", help="extra dzcache options, comma-separated")
    ap.add_argument("--icount", default=None, help="run.sh --icount shift (omit for time control / host clock)")
    ap.add_argument("--slices", type=int, default=200, help="stop this many 100M-instruction slices after PLAY")
    ap.add_argument("--warm", type=int, default=5_000_000_000, help="SCOPE totals start this many instructions after PLAY")
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument("--hotblocks", action="store_true",
                    help="also run QEMU's libhotblocks, for a per-symbol instruction profile: scripts/dz_bucket.py "
                         "<out>/plugin.log <elf>")
    ap.add_argument("--lean", action="store_true",
                    help="fast flat-CPI timing (timectl=1,cachesim=0 in --plugin): no per-render/per-function accounting")
    ap.add_argument("--scopes", default=None,
                    help="full mode: '|'-separated function name prefixes (demangled, e.g. 'Sound::noteOn(') for the "
                         "plugin's s0..s5 instead of the probe's tick scopes; written to <out>/scopes.txt")
    args = ap.parse_args()

    a = dzprobe.probe_addresses(args.elf)
    if args.scopes:
        prefixes = args.scopes.split("|")
        syms = dzprobe.symbols(args.elf)
        a["scopes"] = [dzprobe.find(syms, p) for p in prefixes]
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "scopes.txt"), "w") as f:
            for i, (p, addr) in enumerate(zip(prefixes, a["scopes"])):
                f.write("s%d %#x %s\n" % (i, addr, p))
    os.makedirs(args.out, exist_ok=True)
    log = os.path.abspath(os.path.join(args.out, "plugin.log"))
    lib = os.path.join(REPO, "build", "plugins", "libdzcache." + ("dylib" if sys.platform == "darwin" else "so"))
    opts = ["ttb=%#x" % a["ttb"], "l2c=0x3FFFF000", "mark=%#x" % a["mark"], "slice=100000000", "phasea=63"]
    if args.lean:
        opts.append("lean=1")
    else:
        opts += ["render=%#x" % a["render"], "timer=%#x" % a["timer"], "warm=%d" % args.warm]
        opts += ["s%d=%#x" % (i, addr) for i, addr in enumerate(a["scopes"])]
    if args.plugin:
        opts.append(args.plugin)
    sock = "/tmp/dzperf_%d.sock" % os.getpid()
    extra = ["-plugin", "%s,%s" % (lib, ",".join(opts))]
    if args.hotblocks:
        # Writes its report to the same log when QEMU quits
        hb = os.path.join(REPO, "qemu", "build", "contrib", "plugins",
                          "libhotblocks." + ("dylib" if sys.platform == "darwin" else "so"))
        extra += ["-plugin", hb + ",inline=on,limit=0"]
    extra += ["-d", "plugin", "-D", log]

    def slices_after_mark():
        try:
            text = open(log, errors="replace").read()
        except FileNotFoundError:
            return -1
        m = text.find("MARK")
        return text[m:].count("SLICE") if m >= 0 else -1

    ringdir = os.path.join(args.out, "rings")
    os.makedirs(ringdir, exist_ok=True)
    dumps = []

    import subprocess
    nl = [l.split() for l in subprocess.run(["arm-none-eabi-nm", "-S", args.elf], capture_output=True, text=True).stdout.splitlines() if l.endswith(" dzNoteLog")][0]
    nl_addr, nl_size = int(nl[0], 16), int(nl[1], 16)
    nldumps = []

    def dump_ring(dz):
        p = os.path.abspath(os.path.join(ringdir, "n%04d.bin" % len(nldumps)))
        try:
            dz.q.execute("stop")
            dz.q.execute("memsave", val=nl_addr, size=nl_size, filename=p, **{"cpu-index": 0})
        finally:
            dz.q.execute("cont")
        nldumps.append(p)
        if "renders" not in a:
            return
        p = os.path.abspath(os.path.join(ringdir, "r%04d.bin" % len(dumps)))
        try:
            dz.q.execute("stop")
            dz.q.execute("memsave", val=a["renders"], size=a["renders_size"], filename=p, **{"cpu-index": 0})
        finally:
            dz.q.execute("cont")
        dumps.append(p)

    with DelugeSession.launch(firmware=args.elf, sd=args.sd, icount=args.icount, headless=True, audio="none",
                              sock=sock, run_log=os.path.join(args.out, "run.log"), boot_wait=5,
                              extra_qemu=extra) as dz:
        t0 = time.time()
        while slices_after_mark() < args.slices and time.time() - t0 < args.timeout:
            time.sleep(10)
            if slices_after_mark() >= 0:
                dump_ring(dz)
        dz.q.execute("stop")
        dz.q.execute("memsave", val=a["probe"], size=a["probe_size"],
                     filename=os.path.abspath(os.path.join(args.out, "probe.bin")), **{"cpu-index": 0})
        if "renders" in a:
            dz.q.execute("memsave", val=a["renders"], size=a["renders_size"],
                         filename=os.path.abspath(os.path.join(args.out, "renders.bin")), **{"cpu-index": 0})
        dump_ring(dz)
        merge_rings(dumps, os.path.join(args.out, "renders_all.bin"))
        merge_notelog(nldumps, os.path.join(args.out, "notelog.bin"))
        print("%s: %d slices after PLAY, %.0f s wall" % (args.out, slices_after_mark(), time.time() - t0))


def merge_notelog(paths, out):
    import struct
    ents = {}
    for p in paths:
        raw = open(p, "rb").read()
        magic, index, esize, n = struct.unpack_from("<IIII", raw)
        if magic != 0xD2D2D2D4:
            continue
        for k in range(max(0, index - n), index):
            ents[k] = raw[16 + (k % n) * esize: 16 + (k % n + 1) * esize]
    ks = sorted(ents)
    missing = (ks[-1] - ks[0] + 1 - len(ks)) if ks else 0
    with open(out, "wb") as f:
        f.write(b"".join(ents[k] for k in ks))
    print("merge_notelog: %d entries (%d missing) -> %s" % (len(ks), missing, out))


def merge_rings(paths, out):
    import struct
    ents = {}
    hdr = None
    for p in paths:
        raw = open(p, "rb").read()
        magic, index, esize, n = struct.unpack_from("<IIII", raw)
        if magic != dzprobe.RENDER_MAGIC:
            continue
        hdr = (magic, esize)
        for k in range(max(0, index - n), index):
            ents[k] = raw[16 + (k % n) * esize: 16 + (k % n + 1) * esize]
    if not ents:
        return
    lo, hi = min(ents), max(ents)
    missing = [k for k in range(lo, hi + 1) if k not in ents]
    if missing:
        print("merge_rings: %d renders missing (dump interval too long), first %d" % (len(missing), missing[0]))
    with open(out, "wb") as f:
        n = hi - lo + 1
        f.write(struct.pack("<IIII", hdr[0], hi + 1, hdr[1], n))
        # entry k lives at slot k % n, as in the firmware ring
        slots = [b"\0" * hdr[1]] * n
        for k, e in ents.items():
            slots[k % n] = e
        f.write(b"".join(slots))
    print("merge_rings: %d renders %d..%d -> %s" % (n, lo, hi, out))


if __name__ == "__main__":
    main()
