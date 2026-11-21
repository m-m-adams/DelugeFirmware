#!/usr/bin/env python3
# Adds the E2 note log hooks to a probe-carrying tree (cwd = repo root). Files dz_notelog.{h,cpp} copied from commit 8e0bb70.
import subprocess
for f in ("src/deluge/io/debug/dz_notelog.cpp", "src/deluge/io/debug/dz_notelog.h"):
    open(f, "w").write(subprocess.run(["git", "show", "8e0bb70:" + f], capture_output=True, text=True, check=True).stdout)
p = "src/deluge/processing/sound/sound.cpp"
s = open(p).read()
s = s.replace('#include "io/debug/dz_probe.h"',
              '#include "io/debug/dz_notelog.h"\n#include "io/debug/dz_probe.h"\n#include "processing/sound/sound_drum.h"', 1)
name = "isDrum() ? static_cast<SoundDrum*>(this)->getName() : static_cast<SoundInstrument*>(this)->name.get()"
old = "	DZ_SCOPE(dz::SCOPE_NOTE_ON_POST);\n"
assert s.count(old) == 1
s = s.replace(old, old + "	dz::noteLog(%s, noteCodePostArp, velocity, 1);\n" % name)
old = "void Sound::noteOffPostArpeggiator(ModelStackWithSoundFlags* modelStack, int32_t noteCode) {\n"
assert s.count(old) == 1
s = s.replace(old, old + "	dz::noteLog(%s, noteCode, 0, 2);\n" % name)
open(p, "w").write(s)
print("notelog applied")
