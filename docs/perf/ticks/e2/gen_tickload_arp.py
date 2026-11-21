#!/usr/bin/env python3
"""E2: gen_tickload.py with the clip arpeggiator switched ON (synth clip arp, or the kit arp for --kit songs).

  gen_tickload_arp.py --arp-note-mode pattern --arp-sync 7 -o OUT.XML [gen_tickload options...]
Everything else is gen_tickload.py unchanged (imported, ARP_ATTRS replaced)."""
import sys
sys.path.insert(0, "/home/user/perfwork/song")
import gen_tickload as g  # noqa: E402

args = sys.argv[1:]
note_mode, sync = "up", "7"
for flag in ("--arp-note-mode", "--arp-sync"):
    if flag in args:
        i = args.index(flag)
        val = args[i + 1]
        del args[i:i + 2]
        if flag == "--arp-note-mode":
            note_mode = val
        else:
            sync = val
g.ARP_ATTRS = ('mode="off"\n{i}\tsyncLevel="%s"\n{i}\tnumOctaves="2"\n{i}\tsyncType="0"\n{i}\tarpMode="arp"\n'
               '{i}\tnoteMode="%s"\n{i}\toctaveMode="up"\n{i}\tnotePattern="00000100030205010408020A06000D07"' % (sync, note_mode))
# notePattern is fixed (the firmware generates a random one at load when the song has none), so two builds play the
# same PATTERN-mode sequence and can be compared note by note
sys.exit(g.main(args))
