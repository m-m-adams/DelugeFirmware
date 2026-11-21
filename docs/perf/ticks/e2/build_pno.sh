#!/bin/bash
# build_pno.sh BASECOMMIT NAME [PICK...]: throwaway branch e2-build = BASECOMMIT + PICKs, build, copy ELF to elf/e2/NAME.elf
set -e
S=/tmp/claude-0/-home-user/b040d493-9c67-53ac-8251-ba359035122a/scratchpad
mkdir -p $S
cd /home/user/wt/e2-cheap-note-on
B=$1; N=$2; shift 2
git checkout -q -B e2-build "$B"
for p in "$@"; do git cherry-pick "$p" >/dev/null || { echo PICK FAILED $p; git cherry-pick --abort; git checkout -q perf/e2-on-pno; exit 1; }; done
if [ -n "$NOTELOG" ]; then python3 /home/user/perfwork/e2/apply_notelog.py && git add -A src && git commit -q --no-verify -m "debug: E2 note log (not for merging)"; fi
git log --oneline -1
DBT_NO_SYNC=1 DBT_TOOLCHAIN_PATH=/home/user/DelugeFirmware ./dbt build release > $S/build-$N.log 2>&1 || { echo BUILD FAILED; grep -m5 "error" $S/build-$N.log; git checkout -q perf/e2-on-pno; exit 1; }
cp build/Release/deluge.elf /home/user/perfwork/elf/e2/$N.elf
git checkout -q perf/e2-on-pno
echo BUILT $N $(md5sum /home/user/perfwork/elf/e2/$N.elf | cut -c1-8)
