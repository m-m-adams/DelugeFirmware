#!/bin/bash
export RUNNER=/home/user/perfwork/tools/run_emu_rings_pno.py
while read -r elf tag key run rest; do
  [ -z "$elf" ] && continue
  [ -f /home/user/perfwork/runs/e2-$run/renders_all.bin ] && { echo "skip $run"; continue; }
  rm -rf /home/user/perfwork/runs/e2-$run
  echo "== $run"; /home/user/perfwork/e2/run1.sh $elf $tag $key $run $rest
done < /home/user/perfwork/e2/batch_m.txt
echo BATCHM DONE
