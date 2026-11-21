#!/bin/bash
# nl_run.sh ELF TAG KEY SD : note-log run into runs/e2-nl-TAG-KEY (lean, 80 slices)
export PATH=/home/user/perfwork/bin:$PATH
E=/home/user/perfwork/elf/e2/nl-$2-$3.elf
cp "$1" "$E"; python3 /home/user/perfwork/tools/set_song.py "$E" "$3" >/dev/null
python3 /home/user/perfwork/e2/run_emu_notelog.py --elf "$E" --sd "$4" --out /home/user/perfwork/runs/e2-nl-$2-$3 \
   --plugin timectl=1,cachesim=0,cpi=1.45 --lean --slices ${SLICES:-80} 2>&1 | tail -3
