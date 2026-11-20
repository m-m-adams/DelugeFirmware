#!/bin/bash
# batch.sh: lines of "ELF TAG KEY RUN [args]" on stdin, run sequentially
while read -r elf tag key run rest; do
  [ -z "$elf" ] && continue
  echo "== $run"
  /home/user/perfwork/e2/run1.sh $elf $tag $key $run $rest
done
echo BATCH DONE
