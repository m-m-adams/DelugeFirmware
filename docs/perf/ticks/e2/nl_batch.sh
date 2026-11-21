#!/bin/bash
D=/home/user/perfwork/elf/e2; E2=/home/user/perfwork/e2/sd-e2.img; E3=/home/user/perfwork/sd-e3.img
for key_sd in "ARPPAT_U1 $E2" "ARPKITPAT $E2" "ARPUP_U1 $E2" "TICKLOAD_BU6 $E2" "BKIT20U4 $E2" "E3ARP $E3" "E3KARP $E3" "E3DARP $E3" "E3POLY $E3" "E3KPOLY $E3"; do
  set -- $key_sd
  for b in n0m n4m; do
    [ -f /home/user/perfwork/runs/e2-nl-$b-$1/notelog.bin ] && continue
    echo "== $b $1"; /home/user/perfwork/e2/nl_run.sh $D/$b-notelog.elf $b $1 $2
  done
  python3 /home/user/perfwork/e2/notelog_cmp.py /home/user/perfwork/runs/e2-nl-n0m-$1 /home/user/perfwork/runs/e2-nl-n4m-$1 | grep -E "^A:|RESULT|DIFF" | head -5
done
echo NLBATCH DONE
