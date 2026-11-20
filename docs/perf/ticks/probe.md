# Probe firmware (Phase 1B): `perf/tick-probe`

Rebuild of the missing `debug/pmu-render-probe` on current firmware main, compatible with delugemu
`tools/perf/{dzprobe,run_emu,summarize}.py`, plus song-tick instrumentation.

- Worktree / branch: `/home/user/wt/probe`, `perf/tick-probe` (off main 680b93c). Local commits only.
- Probe commit: `e339b27` "debug: dz probe for render and song-tick performance (not for merging)" - one commit, cherry-pickable.
- ELF: `/home/user/perfwork/elf/probe-base.elf` (main 680b93c + probe; dzSongPath "TICKLOAD").
- Code: `src/deluge/io/debug/dz_probe.{h,cpp}` (+ small hooks in audio_engine.cpp, sound.cpp, voice.cpp,
  instrument_clip.cpp, note_row.cpp, playback_handler.cpp, deluge.cpp). `#define DZ_PROBE 1` in `dz_probe.h`;
  with 0 every hook is an empty inline and DZ_SCOPE / DZ_NOINLINE expand to nothing.
- Tools (local copies, delugemu untouched): `/home/user/perfwork/tools/`
  - `dzprobe.py`  FIELDS + TICK_FIELDS, tick-path SCOPE_NAMES / scope addresses, `load_renders()` for the per-render ring
  - `run_emu.py`  upstream run_emu, but imports the local dzprobe (so s0..s5 = tick functions) and also memsaves
    `dzRenders` to `<out>/renders.bin`. Uses `$DELUGEMU` (default `/home/user/synthstromaudible/delugemu`) for
    pylib/plugin/qemu.
  - `summarize.py` upstream summarize + "playing" windows from the probe's own count, voices from voiceSum, and a
    song-tick table (ticks/s, tick share of cycles, tick vs non-tick renders, voice starts, cull split, culls by
    renders since tick, firmware DzScope cycles per call)
  - `ticks.py`  reads `renders.bin`: tick vs non-tick render averages, culls by renders-since-tick, and
    `--list N` dumps the renders around the first N culls.
  - `set_song.py`  patches dzSongPath in an ELF in place.

## Globals (all `extern "C"`, so `nm -C` names are plain)

| symbol | what |
| --- | --- |
| `dzProbe` | window ring, SDRAM (`PLACE_SDRAM_BSS`), 843,796 bytes: magic 0xD2D2D2D2, index, 2048 x 103 uint32 fields, then 12 bytes {numFields=103, version=0x00020001, playIndex} |
| `dzRenders` | per-render ring, SDRAM, 655,376 bytes: magic 0xD2D2D2D3, index (renders recorded), entrySize=40, numEntries=16384, entries |
| `dzSongPath` | `char[64]` = "TICKLOAD" (.data, patchable in the ELF) |
| `dzCommand` | 1 = autoplay at boot (default), 0 = do nothing |
| `dzAutoStage` | 0 off, 1 waiting for card, 2 searching, 3 loading, 4 loaded, 5 play pressed; 0xE1 no SONGS dir, 0xE2 no match, 0xE3 load UI didn't open |
| `dzResult`, `dzLoadIndex`, `dzPlayIndex` | hw_probe.py's AUTO line; Load/Play index = window index when the load started / play was pressed |
| `dzLoadedSong` | file name that was loaded |

## Window fields (100 ms of audioSampleTimer = 4410 samples; a window closes at the first render end past that)

Cycles are PMU cycles: QEMU's PMCCNTR counts 1 GHz of guest (virtual) time; hardware 400 MHz. In `--lean` mode
QEMU's clock advances every few thousand instructions, so very short intervals (one noteOn) are quantised;
sums over many calls are fine.

Upstream FIELDS (63), what's filled:
- `start` audioSampleTimer at the window's first render (upstream semantics unknown; not used by the tools)
- `renders` routine_() calls that rendered; `frames` samples rendered; `maxFrames` largest render
- `maxGap` most cycles from the end of one render to the start of the next
- `maxRender` most cycles of one render (whole routine_(), tick included), with `maxRenderSamples`,
  `maxRenderVoicesStarted`, `maxRenderHops` of that render
- `culls` voices (or audio clips) culled = kill + terminate + forceRelease actions
- `sum_cyc`/`max_cyc` whole routine_() cycles (incl. tick) -> summarize's load%
- `sum_/max_ ins l1i l1d l1dacc istall dstall`: A9 events 0x68, 0x01, 0x03, 0x04, 0x60, 0x61 on counters 0-5
  (only on hardware; QEMU reads 0). `l2rd/l2rdhit` 0.
- `voices` synth voices at window end, `audioVoices` audio-clip voices at window end
- `scopeCalls/Ins/Cyc 0..5` DzScope totals over the window (not "most expensive render")
- 0: maxGapCaller, maxGapTask, maxGapTaskTime, maxTask, maxTaskTime, maxIrq, irqs, late, clustersFinished,
  delay*, tickInvMin/Max

TICK_FIELDS (40, appended):
| field | meaning |
| --- | --- |
| playing | renders while playback was on (summarize uses this to pick windows: the tick song has no audio clips, so upstream's `audioVoices` filter would find nothing) |
| ticks | timer + swung ticks actioned from tickSongFinalizeWindows() |
| tickRenders | renders that actioned >= 1 tick |
| sum_tickCyc / max_tickCyc | cycles spent actioning ticks (actionTimerTick/actionSwungTick+scheduleSwungTick), per render max |
| sum/max_renderCycTick, sum/max_renderCycNoTick | cycles from after the tick to the end of routine_() (renderAudio etc.), for renders with / without a tick |
| startedTick / startedOther / startedPending | allowedToStartVoice() yes: in Sound::noteOn during a tick / Sound::noteOn outside a tick (MIDI, auditioning) / process_postarp_notes (pending note started in a render) |
| refusedTick / refusedOther / refusedPending | allowedToStartVoice() no, same split (tick/other refusals leave a note pending) |
| maxPending | most notes estimated pending: += refusedTick/Other, -= startedPending, reset when not playing. Estimate (notes released before starting aren't subtracted) |
| cullKill / cullTerminate / cullForceRelease | voices culled by each function |
| cullEventsHard / Soft / UnderMin | cullVoices() branches that culled: hard (>= 32 over: kill+terminate+1 force), soft (force release), under MIN_VOICES (force release) |
| cullsAt{0,1,2,3to4,5to8,later} | culls by renders since the last render with a tick. 0 = a render that then had a tick (culls happen in setDireness() at the start of routine_(), before that render's tick); 1 = the render right after a tick render |
| rendersAt{...} | renders in each bucket (normaliser: culls per render) |
| maxDireness | max cpuDireness at render end |
| minQueued | least (128 - space) at render start, samples |
| minMargin (signed) | least predicted margin in setDireness() (queued - songTickCost - predicted render), samples |
| voiceSum / maxVoices | synth voices summed over renders (avg = voiceSum/renders), max |
| maxSongTickCost | most songTickCost.get() seen, samples x 16 |

## DzScope slots (firmware) = plugin s0..s5 (local dzprobe.probe_addresses)

| slot | function | note |
| --- | --- | --- |
| 0 | `PlaybackHandler::actionSwungTick` | all note starts from the song happen under this |
| 1 | `InstrumentClip::processCurrentPos` | per clip |
| 2 | `NoteRow::processCurrentPos` | per note row |
| 3 | `Sound::noteOnPostArpeggiator` | from tick and from pending |
| 4 | `Voice::noteOn` | |
| 5 | `Sound::process_postarp_notes` | firmware scope: one per *pending* note tried (keeps it cheap); plugin s5: every call |

All six are `DZ_NOINLINE` in the probe commit so they are real symbols for the plugin.

## Per-render ring (`dzRenders`, 40-byte entries, last 16384 renders, ~10 s at high load)

`struct.unpack("<IIIIIHHBBBBHBBhHHH")`, fields:
timer (audioSampleTimer at start), cycStart (PMCCNTR at top of routine_()), totalCyc, tickCyc, renderCyc,
space (space in TX buffer at start; queued = 128 - space), samples (rendered), ticks, startedTick, startedPending,
culls, voices, direness (cpuDireness at end), flags (1 playing, 2 hard cull, 4 soft cull, 8 under-min cull,
16 second routine_() of one routine() call, 32 margin valid), margin (setDireness predicted margin),
tickCost (songTickCost x16), refused, pending (estimate).
Gaps between renders = consecutive cycStart minus totalCyc (`load_renders()` gives `dt`).

## Autoplay

At boot `dz::registerAutoplay()` (right after the startup-song task is registered in deluge_main) adds a conditional
task (priority 101, after setupStartupSong's 100) on `isCardReady`. It picks the alphabetically first `*.XML` directly
in `SONGS/` whose name contains `dzSongPath` (case-insensitive), loads it exactly like setupStartupSong()
(`currentSong->setSongFullPath(); openUI(&loadSongUI); loadSongUI.performLoad()`), then 1 s later (once task) calls
`playbackHandler.playButtonPressed(kInternalButtonPressLatency)` — the button's own path, so the plugin's MARK fires.
No match: nothing happens (dzAutoStage = 0xE2). Subfolders (SONGS/VARIANTS) are not searched.

Changing the song: rebuild with another initializer in dz_probe.cpp, or patch the ELF in place (dzSongPath is in
.data, VMA == LMA, so it's in the loaded image):
```sh
/home/user/perfwork/tools/set_song.py /home/user/perfwork/elf/<copy>.elf KIT20   # needs arm-none-eabi-nm/objdump on PATH
```
Setting `dzCommand` to 0 the same way disables autoplay.

## Cherry-picking onto an experiment branch

```sh
cd <experiment worktree>
git cherry-pick <probe commit>     # conflicts, if any, are in the few hook lines
DBT_NO_SYNC=1 DBT_TOOLCHAIN_PATH=/home/user/DelugeFirmware ./dbt build release
cp build/Release/deluge.elf /home/user/perfwork/elf/<name>.elf
```
If an experiment moves voice starts out of Sound::noteOn / process_postarp_notes or changes tickSongFinalizeWindows,
keep the `dz::voiceStartAsked()` / `dz::tickBegin()/tickEnd()` calls next to the new code so the fields keep meaning.

## Reading the rings

`run.sh` wants an absolute `--elf` path. arm-none-eabi tools: `export
PATH=/home/user/DelugeFirmware/toolchain/v22/linux-x86_64/arm-none-eabi-gcc/bin:$PATH`.

```sh
T=/home/user/perfwork/tools
$T/run_emu.py --elf /home/user/perfwork/elf/probe-base.elf --sd <card.img> --out /home/user/perfwork/runs/base-lean \
    --plugin timectl=1,cachesim=0,cpi=1.45 --lean
$T/summarize.py /home/user/perfwork/runs/base-lean            # incl. the song-tick table
$T/ticks.py /home/user/perfwork/runs/base-lean/renders.bin --list 10
```
Manual dump from a running emulator via QMP: `memsave val=<dzProbe addr> size=843796 filename=probe.bin cpu-index=0`
(and the same for dzRenders, size 655376); addresses from `python3 -c "import sys; sys.path.insert(0,
'/home/user/perfwork/tools'); import dzprobe; print(dzprobe.probe_addresses('probe.elf'))"`.

## Boot test (2026-11-20): it works

`runs/probe-smoke`: probe-base.elf, card from `/home/user/perfwork/sdcard` (TICKLOAD.XML, 20 voices per 16th step,
synth-only) built with mksd.sh, `--plugin timectl=1,cachesim=0,cpi=1.45 --lean --slices 60` (50 s wall).
Autoplay loaded TICKLOAD.XML and pressed play (MARK at 157 M instructions, playIndex 2). Both rings parse;
`summarize.py` and `ticks.py` work. Numbers (this song, 60% load, no culls at all):

- load 60%, 21 samples per render, ~20 voices; 23 ticks/s actioned, 1.1% of renders have a tick
- tick render: tick ~100k cycles avg, max ~300k (0.3 ms = ~13 samples); render part 290k either way
- **cpuDireness sits at 12-14 the whole time even at 60% load and margins of 60-90 samples** (direness rises as soon
  as the predicted margin is under ~79), so allowedToStartVoice() allows 1 (direness > 12) or 4 voices per render.
  Of a 20-note step only ~1 starts in the tick; 19 are refused and start from pending over the next ~5 renders
  (4 per render). So with this song the firmware already spreads voice starts over renders; the tick itself is
  then mostly clip/note-row processing plus 19 refused noteOns (noteOnPostArpeggiator ~18k cycles/call, Voice::noteOn
  ~15k, InstrumentClip::processCurrentPos ~16k, NoteRow::processCurrentPos ~6k, per call).
- songTickCost (reserved in every prediction) ~12.8 samples.
- Lean mode quantises PMCCNTR to ~10k cycles (10 us of guest time): tick cycles read 10002, 240504... Use
  sums/averages, or full mode for exact short intervals.

## Open issues / caveats

- Upstream summarize.py (delugemu) would see no "playing" windows for synth-only songs (it filters on audioVoices);
  use the local copy.
- A9 event counters (ins, refills, stalls) only count on hardware; QEMU reads 0. l2rd/l2rdhit, gap-task, irq,
  late-cluster, delay and tickInv fields are left 0.
- `pending` is an estimate (notes released while still pending are never subtracted until playback stops).
- The ring lives in cacheable SDRAM; a hardware dump via a debugger may miss lines still in L1/L2 (fine in QEMU).
