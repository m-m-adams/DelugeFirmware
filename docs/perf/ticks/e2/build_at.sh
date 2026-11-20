#!/bin/bash
# build_at.sh COMMIT OUTNAME : probe branch = COMMIT + probe commit, build, copy ELF to elf/e2/OUTNAME.elf
set -e
cd /home/user/wt/e2-cheap-note-on
git checkout -q perf/e2-cheap-note-on-probe
git reset -q --hard "$1"
if [ -z "$NOPROBE" ]; then git cherry-pick e339b27 >/dev/null; fi
DBT_NO_SYNC=1 DBT_TOOLCHAIN_PATH=/home/user/DelugeFirmware ./dbt build release > /tmp/claude-0/-home-user/b040d493-9c67-53ac-8251-ba359035122a/scratchpad/build-$2.log 2>&1 || { echo BUILD FAILED; tail -20 /tmp/claude-0/-home-user/b040d493-9c67-53ac-8251-ba359035122a/scratchpad/build-$2.log; git checkout -q perf/e2-cheap-note-on; exit 1; }
cp build/Release/deluge.elf /home/user/perfwork/elf/e2/$2.elf
git checkout -q perf/e2-cheap-note-on
echo BUILT $2 $(md5sum /home/user/perfwork/elf/e2/$2.elf | cut -c1-8)
