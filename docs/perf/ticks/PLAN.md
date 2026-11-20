# Song-tick cost amortization investigation — shared plan

Goal: measure how much the song tick (PlaybackHandler::actionTimerTick / actionSwungTick, run from
AudioEngine::tickSongFinalizeWindows() right before renderAudio()) adds to the render on top of DSP, show that
it causes culls, and try ways to spread that cost over several renders.

## Layout
- Emulator checkout:   /home/user/synthstromaudible/delugemu   (read its .claude/skills/measure-firmware-performance/SKILL.md and tools/perf/README.md)
- Firmware main:       /home/user/DelugeFirmware  (toolchain in toolchain/; branch claude/pensive-euler-9rstjv — do not commit there)
- Probe worktree:      /home/user/wt/probe  (branch perf/tick-probe, off main)
- Shared work dir:     /home/user/perfwork
    song/      test song generator + generated XMLs
    sdcard/    minimal card content (SONGS/...) -> built into an image with delugemu scripts/mksd.sh
    runs/      emulator run outputs (one dir per run)
    notes/     each agent writes <agent>.md with findings, commands, gotchas
    elf/       stable copies of built ELFs

Scope: current firmware main only. Ignore other/old branches (their ideas are already merged to main).

## Phases
1. (parallel)
   A. Emulator build on Linux: QEMU + patches, dzcache plugin, libhotblocks, SD image.
   B. Probe firmware: rebuild the missing debug/pmu-render-probe on current main (dzProbe ring compatible with
      tools/perf/dzprobe.py, autoplay of dzSongPath at boot) + tick-specific fields.
   C. Test song: generator for a song that starts ~20 voices on every sequencer step.
2. Baseline: run probe + test song at Deluge speed (lean, full with tick scopes, hotblocks). Where does tick time go,
   how big is it vs render, are culls tied to ticks?
3. Experiments (parallel, one worktree each, A/B against baseline):
   E1 tick-aware prediction/culling (only budget songTickCost when a tick is due in this window) + voice-start cap tuning
   E2 cheaper/split voice start on main (where does per-voice start cost go now that starts are already capped per render?)
   E3 lookahead / spread the tick itself (process clips/note rows ahead in spare time, or a subset per render)
   E4 deferred allocation: no GeneralMemoryAllocator allocation inside AudioEngine::routine_ (tick or render).
      New GMA interface: request an eventual allocation, get back a handle/slot where the pointer will appear;
      a low-priority task does the actual alloc. ModFX/delay/grain etc. skip rendering until their buffer arrives.
      Worktree /home/user/wt/e4-deferred-alloc, branch perf/e4-deferred-alloc (probe commit cherry-picked on top for A/B).
4. Report.
