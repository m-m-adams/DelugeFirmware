#!/bin/bash
# usage: runbatch.sh STAGE SONG:N [SONG:N ...]   (runs sequentially; N = run index)
export PATH=/home/user/perfwork/bin:$PATH
P=/home/user/perfwork; T=$P/tools
stage=$1; shift
for spec in "$@"; do
  song=${spec%%:*}; n=${spec##*:}
  key=$song; [ "$song" = KIT20 ] && key=D_KIT20
  elf=$P/elf/e1-$stage-$song.elf
  [ -f $elf ] || { cp $P/elf/e1/probe-e1-$stage.elf $elf && python3 $T/set_song.py $elf $key >/dev/null || exit 1; }
  out=$P/runs/e1-$stage-$song-lean$n
  [ -d $out ] && { echo "skip $out"; continue; }
  echo "=== $(date +%T) $out"
  $T/run_emu_rings.py --elf $elf --sd $P/sd-baseline.img --out $out --plugin timectl=1,cachesim=0,cpi=1.45 --lean > $out.out 2>&1 || echo "FAILED $out"
  python3 $T/e1report.py $out | tail -1
done
