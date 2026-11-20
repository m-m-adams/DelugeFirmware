# Emulator setup on Linux (Phase 1A)

Status: everything works on this container (Ubuntu 24.04, 4-core Xeon 2.1 GHz). **No delugemu scripts needed
patching**: `integrate.sh`, `build.sh`, `build-plugins.sh`, `mksd.sh` and `tools/perf/run_emu.py` all have working
Linux branches (`.so`, mtools). The only local additions are wrapper scripts in `/home/user/perfwork/bin`.

## Environment

```sh
export PATH=/home/user/perfwork/bin:$PATH        # arm-none-eabi-{nm,addr2line,c++filt,objdump,readelf,size,strings,gdb,objcopy}
export DELUGEMU=/home/user/synthstromaudible/delugemu
cd $DELUGEMU                                      # run_emu.py / pylib find the repo from their own path; cwd doesn't matter
```

`/home/user/perfwork/bin/arm-none-eabi-*` are symlinks to
`/home/user/DelugeFirmware/toolchain/v22/linux-x86_64/arm-none-eabi-gcc/bin/` (xPack GCC 14.2.1, binutils 2.43.1).
`dzprobe.py`, `cache_report.py`, `scripts/dz_bucket.py`, `dz_topblocks.py`, `flame.py` call them by bare name, so the
PATH export is required.

## Paths

| what | path |
| --- | --- |
| QEMU binary | `$DELUGEMU/qemu/build/qemu-system-arm` (`-M deluge` registered) |
| dzcache plugin | `$DELUGEMU/build/plugins/libdzcache.so` (also `libdzstack.so`) |
| hotblocks plugin | `$DELUGEMU/qemu/build/contrib/plugins/libhotblocks.so` (all QEMU contrib plugins are built by default) |
| SD image | `/home/user/perfwork/sd.img` (4 GiB sparse, ~1 MB on disk, FAT32, 32 KiB clusters) from `/home/user/perfwork/sdcard` |
| stock ELF | `/home/user/perfwork/elf/stock.elf` (main 680b93c, Release, built in worktree `/home/user/wt/stock`) |
| smoke scripts | `/home/user/perfwork/bin/smoke_boot.py`, `/home/user/perfwork/bin/smoke_plugin.py` |

## (Re)build commands

```sh
cd /home/user/synthstromaudible/delugemu
# once: fetch the pinned QEMU (v11.0.1, 6e9a825c) shallowly. ~10 s from gitlab.
git submodule update --init --depth 1 qemu
# deps (already installed): dosfstools mtools libfdt-dev libslirp-dev zlib1g-dev flex bison python3-venv
#                           libglib2.0-dev libpixman-1-dev   (ninja, pkg-config, gcc 13 were present)
scripts/integrate.sh                      # symlinks src/ into qemu/hw/deluge, applies qemu-patches/0001-0008
# first configure+build, the same as build.sh does but with -j4 (build.sh runs plain `ninja`, i.e. nproc+2 jobs):
mkdir -p qemu/build && (cd qemu/build && ../configure --target-list=arm-softmmu --disable-werror)
ninja -j4 -C qemu/build                   # ~7 min wall with a firmware build running alongside
# after that, incremental rebuilds (e.g. after editing src/ or qemu-patches/):
scripts/build.sh                          # re-runs integrate.sh (idempotent) + ninja
scripts/build-plugins.sh                  # -> build/plugins/libdzcache.so, libdzstack.so (a second)
```

Configure found no SDL / GTK / PulseAudio / ALSA (not installed), so the binary has only `-display none` and the
`none`/`oss`/`wav` audiodevs. That's all headless perf work needs (`DelugeSession` passes `--display none --audio none`).
`run.sh --display console` will not work here.

## SD image

```sh
/home/user/perfwork/bin/mksd                       # /home/user/perfwork/sdcard -> /home/user/perfwork/sd.img
/home/user/perfwork/bin/mksd <content-dir> <img>   # e.g. a card holding only one song variant
```

It's a thin wrapper around `$DELUGEMU/scripts/mksd.sh <dir> <img>` (Linux branch: sparse `dd`, `mkfs.fat -F 32 -S 512
-s 64`, `mcopy -s`), that refuses an empty dir (mksd.sh's `mcopy "$dir"/*` fails on one) and lists the result with
`mdir`. Verified with `fsck.fat -n` and `file` (sectors/cluster 64). Long file names survive (VFAT LFN).
mksd.sh does `rm -f` then recreates the image, so an emulator already running keeps its old (unlinked) copy; it
doesn't see the new content, but isn't corrupted either. Don't point a firmware that writes (save, settings) at the
shared image; work on a copy.

Current content: `SONGS/TICKLOAD.XML` + `SONGS/VARIANTS/*.XML` (Phase 1C). The placeholder I made at first is gone.

## Smoke tests (done, all pass)

1. **Stock firmware build** in a detached worktree (main checkout untouched):
   ```sh
   git -C /home/user/DelugeFirmware worktree add /home/user/wt/stock HEAD --detach
   cd /home/user/wt/stock && DBT_NO_SYNC=1 DBT_TOOLCHAIN_PATH=/home/user/DelugeFirmware ./dbt build release
   cp build/Release/deluge.elf /home/user/perfwork/elf/stock.elf
   ```
   9 min 21 s wall (competing with the QEMU build). No submodules in DelugeFirmware, so DBT_NO_SYNC is harmless.
   Don't pass `-S` to `dbt build` (it's forwarded to cmake and fails: "No source directory specified for -S").
2. **Boot** (`smoke_boot.py [elf] [outdir]`): `DelugeSession.launch(..., headless=True, audio="none",
   int_log=...)`, 12 s boot wait: OLED shows the default new song (`SECTION 1` screen), `assert_no_aborts()` passes,
   screenshot works. Output in `/home/user/perfwork/runs/smoke/`.
3. **dzcache in timectl/lean mode** (`smoke_plugin.py [elf] [outdir] [secs]`): extra_qemu
   `-plugin build/plugins/libdzcache.so,timectl=1,cachesim=0,cpi=1.45,lean=1,ttb=0x20020000,l2c=0x3FFFF000,mark=<PlaybackHandler::playButtonPressed>,slice=100000000,phasea=63 -d plugin -D <log>`.
   Writes `SLICE n insns=... cycles=...` lines (cycles = 1.45 x insns as expected) and a `MARK` line when PLAY is
   pressed via QMP. Stock ELF: `ttb_mmu1_base` = 0x20020000.
4. **run_emu.py end to end** with the Phase 1B probe ELF and TICKLOAD on `sd.img`, plus `--hotblocks` in a separate
   run: probe.bin + plugin.log written, hotblocks report appended to plugin.log, `scripts/dz_bucket.py` works on it,
   `/home/user/perfwork/tools/summarize.py` reads the run (song plays: 23.5 ticks/s, ~20 voices).

## Launching a run

```sh
export PATH=/home/user/perfwork/bin:$PATH; cd /home/user/synthstromaudible/delugemu
tools/perf/run_emu.py --elf /home/user/perfwork/elf/<name>.elf --sd /home/user/perfwork/sd.img \
    --out /home/user/perfwork/runs/<name>-lean --plugin timectl=1,cachesim=0,cpi=1.45 --lean [--slices 200] [--hotblocks]
```

(The probe agent's `/home/user/perfwork/tools/run_emu.py` is the same with tick scopes and a `renders.bin` dump;
prefer it for probe ELFs.) Copy the ELF to a stable path first; the firmware build overwrites `build/Release`.
Run long runs in the background and wait for the `<out>: N slices after PLAY, T s wall` line; `--timeout` (default
3600 s) bounds a run that never reaches PLAY (e.g. song missing: MARK never appears, it prints `-1 slices`).

### Timing on this host

Lean run, probe-base.elf + TICKLOAD, `--slices 100`: **110 s wall for 109 slices** (10.9 G guest instructions =
39.5 s of Deluge time at cpi 1.45), total 1 min 55 s including launch/teardown. So about **100 M guest
instructions per wall second, ~0.36x Deluge real time**, about 2.8x slower than the README's Mac numbers
(200 slices in ~70 s). Estimates: lean 200 slices ~3.5-4 min; full (non-lean, per-function) ~4.5 min x 2.8 ~
12-13 min; cache model ~8 min x 2.8 ~ 22 min. With 4 cores, run at most ~2-3 emulators at once (each uses
~1.3 cores, see below), fewer while firmware is building.

Note: with that probe build the MARK (`playButtonPressed`) fires at slice 1 (157 M instructions, probe window 2):
boot + autoload of the small TICKLOAD song is that quick, so "slices after PLAY" covers nearly the whole run, and the
first seconds after PLAY are the cold phase (summarize skips 25 s by default).

## Gotchas

- **CPU use per emulator ~1.3 cores**: the TCG vCPU thread ~80%, main loop ~40%, IO thread ~10%; high system time
  (sys ~ user) from BQL futex traffic and timer/eventfd wakeups (strace: ~11k futex + 7k write per second). That's
  how the device models run; nothing to fix for this work, but it caps parallelism on 4 cores.
- **One `-D` per QEMU**: `-d int -D int.log` (DelugeSession `int_log=`) and `-d plugin -D plugin.log` share one log
  file option, so you can't have `assert_no_aborts()` and a separate plugin log in the same run. Use `-d plugin,int`
  into one file if you need both (the int lines are noisy).
- **WFE/WFI warning**: dzcache prints once per run "the vCPU halted (WFE/WFI) under time control; the guest clock
  is frozen..." for both stock and probe firmware. The run carries on (SLICEs keep coming, MARK, song plays), so it's a
  short halt somewhere at boot; ignore unless a run stalls.
- `run.log` contains raw serial-port bytes (the firmware's UART), not just run.sh messages.
- QMP sockets: keep the path short (`/tmp/dz_*.sock`); run_emu uses `/tmp/dzperf_<pid>.sock`, so parallel runs
  don't collide. Give your own DelugeSession scripts distinct `sock=` values when running in parallel.
- Don't measure without `timectl=1` (or `--icount`): host-clock runs are idle-diluted and host hiccups show as
  fake culls.
- `scripts/build.sh` re-runs `integrate.sh` every time (idempotent); the qemu submodule shows clean in
  `git status` because touched files are `--skip-worktree`d. `scripts/integrate.sh --undo` reverts.
- delugemu checkout: no local modifications (`git status` clean apart from the populated submodule and the
  gitignored `build/`).

## Local changes to delugemu

None. Additions outside the repo only:
- `/home/user/perfwork/bin/arm-none-eabi-*` symlinks
- `/home/user/perfwork/bin/mksd` (wrapper above)
- `/home/user/perfwork/bin/smoke_boot.py`, `/home/user/perfwork/bin/smoke_plugin.py`

Logs of the builds: `/home/user/perfwork/notes/.qemu_{fetch,configure,build}.log`, `.stock_build.log`.
