#!/bin/bash
# usage: rb2.sh STAGE SONG:N ...   ELF = /home/user/perfwork/elf/e1p/probe-STAGE.elf
export PATH=/home/user/perfwork/bin:$PATH
P=/home/user/perfwork; T=$P/tools
stage=$1; shift
for spec in "$@"; do
  song=${spec%%:*}; n=${spec##*:}
  key=$song; [ "$song" = KIT20 ] && key=D_KIT20
  elf=$P/elf/e1p/e1p-$stage-$song.elf
  [ -f $elf ] || { cp $P/elf/e1p/probe-$stage.elf $elf && python3 $T/set_song.py $elf $key >/dev/null || exit 1; }
  out=$P/runs/e1p-$stage-$song-lean$n
  [ -f $out/renders_all.bin ] && { echo "skip $out"; continue; }
  rm -rf $out
  echo "=== $(date +%T) $out"
  $T/run_emu_rings_pno.py --elf $elf --sd $P/sd-baseline.img --out $out --plugin timectl=1,cachesim=0,cpi=1.45 --lean > $out.out 2>&1 || echo "FAILED $out"
  python3 $T/tb_report.py $out | tail -1
done
