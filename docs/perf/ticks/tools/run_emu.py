#!/usr/bin/env python3
"""Run the dz probe firmware in the emulator under the dzcache plugin and collect its reports.

The probe firmware loads and plays its song by itself, so no input is needed. Host audio is never connected.
Writes <out>/plugin.log (SLICE/MARK/R/SCOPE lines), <out>/probe.bin (the probe's window ring), <out>/renders.bin
(the tick probe's per-render ring, dzRenders) and <out>/run.log.

Local copy of delugemu tools/perf/run_emu.py for the tick probe: uses the local dzprobe.py (tick-path scopes s0..s5)
and also dumps dzRenders. The emulator checkout is $DELUGEMU (default /home/user/synthstromaudible/delugemu).

  tools/perf/run_emu.py --elf probe.elf --out runs/cpi145 --plugin timectl=1,cachesim=0,cpi=1.45 --lean
  tools/perf/run_emu.py --elf probe.elf --out runs/ic1 --icount 1 --plugin cachesim=0
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.environ.get("DELUGEMU", "/home/user/synthstromaudible/delugemu")
sys.path.insert(0, os.path.join(REPO, "pylib"))
sys.path.insert(0, HERE)
import dzprobe
from delugemu import DelugeSession


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--elf", required=True, help="probe firmware ELF")
    ap.add_argument("--sd", default=os.path.join(REPO, "build", "deluge_sd.img"))
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument(
        "--plugin", default="cachesim=0", help="extra dzcache options, comma-separated"
    )
    ap.add_argument(
        "--icount",
        default=None,
        help="run.sh --icount shift (omit for time control / host clock)",
    )
    ap.add_argument(
        "--slices",
        type=int,
        default=200,
        help="stop this many 100M-instruction slices after PLAY",
    )
    ap.add_argument(
        "--warm",
        type=int,
        default=5_000_000_000,
        help="SCOPE totals start this many instructions after PLAY",
    )
    ap.add_argument("--timeout", type=float, default=3600)
    ap.add_argument(
        "--hotblocks",
        action="store_true",
        help="also run QEMU's libhotblocks, for a per-symbol instruction profile: scripts/dz_bucket.py "
        "<out>/plugin.log <elf>",
    )
    ap.add_argument(
        "--lean",
        action="store_true",
        help="fast flat-CPI timing (timectl=1,cachesim=0 in --plugin): no per-render/per-function accounting",
    )
    args = ap.parse_args()

    a = dzprobe.probe_addresses(args.elf)
    os.makedirs(args.out, exist_ok=True)
    log = os.path.abspath(os.path.join(args.out, "plugin.log"))
    lib = os.path.join(
        REPO,
        "build",
        "plugins",
        "libdzcache." + ("dylib" if sys.platform == "darwin" else "so"),
    )
    opts = [
        "ttb=%#x" % a["ttb"],
        "l2c=0x3FFFF000",
        "mark=%#x" % a["mark"],
        "slice=100000000",
        "phasea=63",
    ]
    if args.lean:
        opts.append("lean=1")
    else:
        opts += [
            "render=%#x" % a["render"],
            "timer=%#x" % a["timer"],
            "warm=%d" % args.warm,
        ]
        opts += ["s%d=%#x" % (i, addr) for i, addr in enumerate(a["scopes"])]
    if args.plugin:
        opts.append(args.plugin)
    sock = "/tmp/dzperf_%d.sock" % os.getpid()
    extra = ["-plugin", "%s,%s" % (lib, ",".join(opts))]
    if args.hotblocks:
        # Writes its report to the same log when QEMU quits
        hb = os.path.join(
            REPO,
            "qemu",
            "build",
            "contrib",
            "plugins",
            "libhotblocks." + ("dylib" if sys.platform == "darwin" else "so"),
        )
        extra += ["-plugin", hb + ",inline=on,limit=0"]
    extra += ["-d", "plugin", "-D", log]

    def slices_after_mark():
        try:
            text = open(log, errors="replace").read()
        except FileNotFoundError:
            return -1
        m = text.find("MARK")
        return text[m:].count("SLICE") if m >= 0 else -1

    with DelugeSession.launch(
        firmware=args.elf,
        sd=args.sd,
        icount=args.icount,
        headless=True,
        audio="none",
        sock=sock,
        run_log=os.path.join(args.out, "run.log"),
        boot_wait=5,
        extra_qemu=extra,
    ) as dz:
        t0 = time.time()
        while slices_after_mark() < args.slices and time.time() - t0 < args.timeout:
            time.sleep(10)
        dz.q.execute(
            "memsave",
            val=a["probe"],
            size=a["probe_size"],
            filename=os.path.abspath(os.path.join(args.out, "probe.bin")),
            **{"cpu-index": 0},
        )
        if "renders" in a:
            dz.q.execute(
                "memsave",
                val=a["renders"],
                size=a["renders_size"],
                filename=os.path.abspath(os.path.join(args.out, "renders.bin")),
                **{"cpu-index": 0},
            )
        print(
            "%s: %d slices after PLAY, %.0f s wall"
            % (args.out, slices_after_mark(), time.time() - t0)
        )


if __name__ == "__main__":
    main()
