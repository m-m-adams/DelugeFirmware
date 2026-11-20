#!/usr/bin/env python3
"""Patch dzSongPath (the file-name substring of the song the probe autoplays) in a probe ELF, in place.

set_song.py probe.elf KIT20
"""

import subprocess
import sys


def main():
    elf, new = sys.argv[1], sys.argv[2].encode()
    if len(new) >= 64:
        sys.exit("name too long (max 63)")
    nm = subprocess.run(
        ["arm-none-eabi-nm", elf], capture_output=True, text=True, check=True
    ).stdout
    addr = next(
        int(l.split()[0], 16) for l in nm.splitlines() if l.endswith(" dzSongPath")
    )
    hdr = subprocess.run(
        ["arm-none-eabi-objdump", "-h", elf], capture_output=True, text=True, check=True
    ).stdout
    for line in hdr.splitlines():
        p = line.split()
        if len(p) >= 6 and p[0].isdigit():
            size, vma, lma, off = (
                int(p[2], 16),
                int(p[3], 16),
                int(p[4], 16),
                int(p[5], 16),
            )
            if vma <= addr < vma + size:
                if vma != lma:
                    sys.exit("dzSongPath's section has VMA != LMA; rebuild instead")
                data = bytearray(open(elf, "rb").read())
                o = off + addr - vma
                old = bytes(data[o : o + 64]).split(b"\0")[0]
                data[o : o + 64] = new.ljust(64, b"\0")
                open(elf, "wb").write(data)
                print("dzSongPath: %r -> %r" % (old.decode(), new.decode()))
                return
    sys.exit("dzSongPath not in any section")


if __name__ == "__main__":
    main()
