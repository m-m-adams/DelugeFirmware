# Song-tick performance investigation (not for merging)

Notes from investigating whether the song tick's cost on top of the render causes voice culling, measured in the
delugemu emulator at calibrated Deluge speed.

- `PLAN.md`: the plan and the experiments (E1-E4).
- `probe.md`: the tick probe in this branch's top commit (`src/deluge/io/debug/dz_probe.{h,cpp}`).
- `baseline.md`: baseline measurements on main 680b93c, cull attribution, tick breakdown, direness.
- `emulator.md`: building delugemu and the plugins on Linux.
- `song.md`: the TICKLOAD test song generator (now `tools/perf/gen_tickload.py` in delugemu).
- `tools/`: the measurement scripts used (local copies of delugemu `tools/perf` plus cullattr/cullsim).

Experiment notes live on their own branches under `perf/ticks/claude-*` in `docs/perf/ticks/`.
Paths in the notes refer to the investigation's working directory (`/home/user/perfwork`), which isn't included.
