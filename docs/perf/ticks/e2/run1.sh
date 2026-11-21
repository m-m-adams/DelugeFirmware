#!/bin/bash
# run1.sh SRC_ELF TAG KEY RUN [extra run_emu_rings args...]
# copies SRC_ELF to elf/e2/e2-TAG-KEY.elf, patches the song, runs the emulator into runs/e2-RUN
set -e
export PATH=/home/user/perfwork/bin:$PATH
SRC=$1; TAG=$2; KEY=$3; RUN=$4; shift 4
E=/home/user/perfwork/elf/e2/e2-$TAG-$KEY.elf
cp "$SRC" "$E"
python3 /home/user/perfwork/tools/set_song.py "$E" "$KEY" >/dev/null
${RUNNER:-/home/user/perfwork/tools/run_emu_rings.py} --elf "$E" --sd ${SD:-/home/user/perfwork/sd-baseline.img} \
   --out /home/user/perfwork/runs/e2-$RUN --plugin timectl=1,cachesim=0,cpi=1.45 "$@" 2>&1 | tail -2
