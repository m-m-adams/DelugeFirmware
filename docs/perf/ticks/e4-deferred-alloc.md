# E4: deferred allocation (no GMA work inside AudioEngine::routine)

Worktree `/home/user/wt/e4-deferred-alloc`, branch `perf/e4-deferred-alloc` (off main 680b93c, local commits only):

| commit | what |
|---|---|
| `a0644ca` | GMA: `DeferredAlloc` handle + request table + `deallocDeferred()` + `serviceDeferred()` task, audio-routine depth flag and the "synchronous GMA call inside the audio routine" counters (`deferredAllocStats`) |
| `14d2b32` | Mod FX delay line, `GranularProcessor` and its 512 KiB `GrainBuffer` (Stealable) requested through `DeferredAlloc` |
| `d7be19c` | `Delay` buffers requested through `DeferredAlloc`, `DelayBuffer::discard()` deferred |
| `67015cd` | Song tick: skip `ResizeableArray`'s opportunistic `GMA::extend()` probe in the audio routine, `Sound::voices_` capacity grown ahead of need by the deferred task |
| `0156e4d` | `deferredAllocStats`: request latency (queue -> publish) |

Measurement-only branches (not for merging):
- `perf/e4-deferred-alloc-probe` (worktree `/home/user/wt/e4-probe`) = first 4 E4 commits + probe e339b27
  (cherry-picked as `bf6cfd3`) + the latency commit (`3f7b477`). ELFs: `/home/user/perfwork/elf/e4-probe.elf`
  (without the latency stats; `e4-probe-{E4FX,FXVARY,GRAIN,TL}.elf` song copies) and `e4-probe2.elf` (with them;
  `e4-probe2-{E4FX,E4RATE,FXVARY}.elf`). Base: `probe-base.elf` copies `e4-base-{E4FX,E4RATE,FXVARY,GRAIN}.elf`.
- `perf/e4-alloc-trace` (worktree `/home/user/wt/e4-trace`) = main + probe + `e576f16` "debug: trace GMA calls made
  inside AudioEngine::routine". ELF `/home/user/perfwork/elf/e4-base-trace.elf` (TICKLOAD), `e4-base-trace-fx.elf`
  (E4FX). Records every GMA call made while `AudioEngine::routine()` runs (outermost caller, two code addresses
  found on the stack, size, pointer, PMU cycles, audioSampleTimer, in-tick flag) into `dzAllocTrace` (SDRAM ring of
  16384 x 32 B).

Tools (new, in `/home/user/perfwork/tools/`):
- `run_emu_e4.py` = `run_emu_rings.py` + per-poll dumps of `dzAllocTrace` (merged to `<out>/alloc_all.bin`) and of
  `deferredAllocStats` (`<out>/stats/sNNNN.bin`, final copy `<out>/deferredAllocStats.bin`).
- `alloc_trace.py RUN_DIR ELF [--list N]`: groups the trace by call site (addr2line, inlined chain).
- `e4_compare.py RUN_DIR...`: per-render stats from `renders_all.bin` for song start (first 2 s after PLAY), steady
  state, and the renders right after each E4FX half-bar automation toggle; prints `deferredAllocStats` if present.

Test song `TICKLOAD_E4FX.XML` (generator copy `/home/user/perfwork/song/gen_tickload_e4.py`, the original
`gen_tickload.py` is untouched; card `/home/user/perfwork/sd_e4.img` from `/home/user/perfwork/sdcard_e4/`, also
copied into `sdcard/SONGS/`; probe key `E4FX`):
```
gen_tickload_e4.py -o .../TICKLOAD_E4FX.XML --fx --e4 --e4-kit 4 --kit-modfx flanger --song-modfx grainFX --delay-auto-clips 2
```
5 synth clips x 4 notes (chorus, flanger, phaser, grainFX, TapeWarble mod FX at depth 0x20000000, delay feedback 15
16th-synced, reverb 15) + a kit clip with 4 synth drums hitting once per beat (phaser, grainFX, TapeWarble, dimension;
delay 15) whose kit-level mod FX is a flanger with **feedback step-automated on for half a bar / at minimum for half a
bar** (so the kit's mod FX buffer is freed and re-allocated in the render every bar), kit delay 15, song-level
grainFX. SYN1 and SYN2 have **delay feedback step automation** (on / off each half bar: the Delay is discarded and
re-allocated in the render every bar). The sparse drums' delays also die away and get re-allocated between hits.
20-24 voices per step, ~85% load, ~16 culls/5 s on main.

`TICKLOAD_E4RATE.XML` (card `/home/user/perfwork/sd_e4b.img`, key `E4RATE`) = E4FX + `--delay-rate-auto-clips 3`:
SYN3-5 also step their delay *rate* every quarter bar (0, +0x40000000, 0, -0x40000000), so `Delay::process()` keeps
making new secondary buffers (11-353 KiB) and freeing old ones in `copySecondaryToPrimary()` - a worst case for
render-time allocation churn (~7.5 allocs + 9.5 frees per second). Also used: the coordinator's
`TICKLOAD_FXVARY` / `TICKLOAD_GRAIN` (card `/home/user/perfwork/sd-fx.img`) and plain `TICKLOAD`.

## 1. Allocation inventory: GMA calls reachable from AudioEngine::routine_()

### (b) Empirical: allocation trace on main (base firmware + probe + trace), lean, Deluge speed

`runs/e4-trace-base-tl` (TICKLOAD, synth only, ~15 s of play) - 520 GMA calls in the audio routine, 488 in the tick:

| kind | count | where | when |
|---|---|---|---|
| extend (2 B) | 470 | `ResizeableArray::insertAtIndex -> attemptMemoryExpansion -> GMA::extend` (song tick, Sound::noteOn -> arpeggiator note arrays; element size 2 = `notesAsPlayed`, inferred) | **steady, ~30/s** for the whole run |
| alloc + dealloc | 17 + 15 | `Sound::acquireVoice -> voices_.push_back` (fast_vector growth 8/16/32/64 B) | first ~0.8 s |
| alloc | 15 | `NoteRow::playNote -> Arpeggiator::noteOn -> ResizeableArray::insertAtIndex` first insertion (160 B `notes`, 10 B `notesAsPlayed`) | first tick only |

`runs/e4-trace-base-fx` (E4FX, ~22 s) - 294 GMA calls, 191 in the tick:

| kind | count | site | size | when |
|---|---|---|---|---|
| extend | 168 | arpeggiator arrays, as above (tick) | 2 | steady |
| alloc | 30 | `Delay::informWhetherActive -> DelayBuffer::init -> allocLowSpeed` (render) | 44,256 | steady: automation + sparse drum delays (~1.4/s) |
| dealloc | 22 | `Delay::informWhetherActive -> discardBuffers -> DelayBuffer::discard` (render) | - | steady |
| alloc | 11 | `GlobalEffectable::processFXForGlobalEffectable -> ModFXProcessor::setupBuffer -> delugeAlloc` (kit mod FX) | 4,096 | steady, every bar |
| dealloc | 11 | `... -> ModFXProcessor::disableBuffer` | - | steady, every bar |
| alloc | 1 + 1 | `ModControllableAudio::enableGrain` (356 B GranularProcessor) -> `GranularProcessor::getBuffer -> allocStealable<GrainBuffer>` | 356 + 524,316 | first render |
| alloc/dealloc | ~26 | `Sound::acquireVoice` voices_ growth | 8..64 | first ~0.1 s |
| alloc | 19 | arpeggiator arrays first insertion | 10..160 | first tick |

`runs/e4-trace-base-E4RATE` (E4RATE, ~23 s) - 1775 GMA calls: 1295 tick `extend` probes (arp arrays of all
6 instruments), 172 `DelayBuffer::init` (44 KiB x68, 11 KiB x36, 86 KiB x35, 345 KiB x33) mostly from
`Delay::initializeSecondaryBuffer`, 221 deallocs (154 from `Delay::copySecondaryToPrimary`), plus the E4FX ones.

Cycle cost of these calls (lean mode quantises PMCCNTR to ~10 us, so averages are rough; see the full-mode run below
too, which is no better: PMCCNTR still advances in ~10 us steps; averages over many calls are unbiased): alloc ~0.9-2 us, dealloc ~1.6 us, extend ~0.7-1 us average, total GMA time in the audio routine
~0.3 ms over 22 s of play. **The allocator is cheap here** - the regions are nearly empty, so `alloc()` finds a free
record on the first binary search and nothing gets stolen; `dealloc()` merges with a neighbour and updates one record.

### (a) Code reading (main 680b93c): everything else reachable from the tick/render

| site | context | GMA call | converted? |
|---|---|---|---|
| `ModFXProcessor::setupBuffer/disableBuffer` (512 x 8 B) | GlobalEffectable render (kit / audio clip / song mod FX switching on/off with depth/feedback/offset at min); Sound: UI (`setModFXType`) | delugeAlloc / delugeDealloc | yes |
| `ModControllableAudio::enableGrain` (GranularProcessor) + `GranularProcessor::getBuffer` (512 KiB Stealable) | GlobalEffectable render on first grain use; `processGrainFX` re-gets a stolen buffer in the render | allocMaxSpeed, allocStealable | yes |
| `~GranularProcessor` (GrainBuffer free) | Sound/Kit destruction (UI) | dealloc | yes (deferred if ever in audio) |
| `Delay::informWhetherActive`, `Delay::initializeSecondaryBuffer` (`DelayBuffer::init`, 1..88200 x 8 B + 160 B) | render (`setupWorkingState`, `process`) | allocLowSpeed | yes |
| `DelayBuffer::discard` (`hasWrapped`, `discardBuffers`, `copySecondaryToPrimary`) | render | dealloc | yes (deferred free) |
| `Stutterer::beginStutter` (`DelayBuffer::init`) | UI / MIDI tasks (mod knob press), not the render | allocLowSpeed | no (stays synchronous; would be counted if it ever ran in the audio routine) |
| `Stutterer::endStutter` (`DelayBuffer::discard`) | UI, and `wontBeRenderedForAWhile()` | dealloc | yes (deferred free via `DelayBuffer::discard`) |
| `ResizeableArray::insertAtIndex -> attemptMemoryExpansion -> extend` (arp note arrays, `Kit::drumsWithRenderingActive`, ...) | tick (note on) | extend | opportunistic probe skipped in the audio routine when the array has room; real growth not converted |
| `ResizeableArray` first insertion / growth (`Arpeggiator::notes`, `notesAsPlayed`, `notesByPattern`; `drumsWithRenderingActive`) | tick | allocMaxSpeed (+dealloc of old) | no: one-time per arpeggiator (`emptyingShouldFreeMemory = false`), see residuals |
| `Sound::acquireVoice -> voices_.push_back` (fast_vector) | tick / render (pending notes) | allocMaxSpeed + dealloc | reserve 4 at construction, growth requested from the deferred task when within 2 of capacity; a burst can still realloc synchronously |
| `VoicePool` / `VoiceSamplePool` / `TimeStretcherPool` (`ObjectPool`, 48 pre-allocated each in `AudioEngine::init`) | tick / render | allocMaxSpeed only when > 48 objects are in use; `recycle()` deallocs only when the pool already holds 48 | no: with <= 48 concurrent voices / voice samples / stretchers they never touch the GMA. Above that, the note would have to start late (see "pools" below) |
| `TimeStretcher::allocateBuffer` / its deallocs | render (sample playback, time-stretch hops) | allocMaxSpeed / dealloc | no (not in the synth/FX songs) |
| `Sample::getPercCache...` (`percCacheClusters`, `percCacheMemory`) | render (time stretching) | allocMaxSpeed / allocLowSpeed | no |
| `Voice::render -> LivePitchShifter` + `AudioEngine::getOrCreateLiveInputBuffer` | render (live-input oscillators with pitch shift) | allocMaxSpeed / dealloc | no |
| `DxEngine` / `DxVoicePool` | first DX7 voice | allocMaxSpeed | no |
| `AudioClip` voice samples (`solicitVoiceSample`) | render | pool | no (pool) |
| stem export (`routine()` offline branch -> recorder card routines) | offline rendering only | various | no |

`operator new` / std containers: nothing in the tick/render apart from `voices_` (fast_vector) and the pools' stacks
(reserved by `repopulate()`, never grow past 48).

**Do deallocs need deferring?** Cost-wise not really: `MemoryRegion::dealloc()` is a header check, a neighbour merge
and one sorted-record update (a binary search plus at most a short memmove of 8-byte records; the tables hold
256-1024 records), measured ~1-2 us. It never steals. But it is unbounded in principle (record moves), and the
`DeferredAlloc` handles need a free path anyway (`reset()`/destructor can run in the render), so E4 defers frees
from the audio routine through the same task: a 64-entry ring, overflow falls back to a synchronous free (counted).
Stealables are special: the destroy callback must run with the free (the GMA calls a Stealable's virtuals while its
header says stealable), so a deferred Stealable free carries its destroy callback and the owner first `detach()`es
it (owner pointer cleared, `inUse` set) so nothing steals it or calls back into the dead owner in between.

## 2. Interface (memory/deferred_alloc.h)

```cpp
enum class AllocRegion : uint8_t { MAX_SPEED, LOW_SPEED, STEALABLE };

class DeferredAlloc {
public:
	using ConstructFn = void (*)(void* memory, uint32_t size, void* arg); // runs right after the allocation
	using DestroyFn = void (*)(void* memory);                             // runs right before the free
	enum class State : uint8_t { EMPTY, QUEUED, READY };

	~DeferredAlloc() { reset(); }               // cancels a queued request or frees the memory
	DeferredAlloc(const DeferredAlloc&) = delete; // registered by address while queued: not copyable / movable

	/// Returns the memory if it's here. Otherwise allocates now if allowed (not in the audio routine), else queues a
	/// request (no allocation to enqueue) and returns nullptr: call again (next render). A queued request for another
	/// size is resized; ready memory of another size is freed (deferred) and re-requested.
	void* request(uint32_t size, AllocRegion region = AllocRegion::MAX_SPEED, ConstructFn construct = nullptr,
	              DestroyFn destroy = nullptr, void* arg = nullptr, bool mayAllocateNow = true);
	void* get() const;    // ready memory or nullptr, never allocates
	void* take();         // caller takes ownership, handle -> EMPTY
	void reset();         // cancel / free (free deferred in the audio routine)
	void forget();        // memory was stolen by the GMA
};

// GeneralMemoryAllocator additions
void deallocDeferred(void* address, DeferredAlloc::DestroyFn destroy = nullptr); // free now, or queue in the audio routine
void serviceDeferred();          // frees + up to 8 allocations; no-op inside the audio routine / while the GMA is locked
void* allocInRegion(uint32_t size, AllocRegion region);
void setDeferredMaintenance(void (*fn)()); // extra work for the same task (used for Sound::voices_ capacity)
uint8_t audioRoutineDepth;       // set by AudioEngine::routine(); allocations are deferred while nonzero
extern "C" DeferredAllocStats deferredAllocStats; // counters incl. syncAllocsInAudio / syncDeallocsInAudio
```

Implementation (memory/deferred_alloc.cpp): a fixed table of 64 request slots (owner, size, region, FIFO seq,
backoff) and a 64-entry free ring, both static in internal RAM - enqueueing never allocates. The service picks the
oldest queued slot that isn't backing off, marks it IN_PROGRESS, allocates, then: cancelled meanwhile -> free;
failed -> back to QUEUED with backoff 128 samples doubling to 65536 (2.9 ms .. 1.5 s); resized meanwhile -> free and
retry; else run the construct callback and publish (`ptr_` then `state_ = READY`, compiler barrier between).
Table full -> `request()` returns nullptr with nothing queued (retried on the next call). The synchronous API is
unchanged; outside the audio routine `request()` just allocates (so loading, UI, MIDI behave exactly as before).

Who runs the service:
- a repeating task "deferred alloc", priority 24 (low band), backoff 0.5 ms, target 2 ms, max 10 ms;
- `AudioEngine::routineWithClusterLoading()`, just before it runs the audio routine - so the render still gets its
  memory during long synchronous operations (song loading etc.) that call it in a loop. That's exactly where the
  audio routine used to allocate, so it adds no new interleaving.

## 3. Threading / cancellation reasoning

- One core, cooperative scheduler, no ISR touches the GMA or these queues -> request/cancel/service interleave only
  at call boundaries; no atomics needed (volatile + compiler barrier for the publish).
- `AudioEngine::routine()` is run by its task and nested via `routineWithClusterLoading()` / `routineForSD()`
  from many places (song loading, UI). It never yields, so the service can't run in the middle of a render.
  The service refuses to run when `audioRoutineLocked` is set (which also covers the places that lock the audio
  routine out while rebuilding objects - `PlaybackHandler` play start, `InstrumentClip` instrument change,
  `Sound::deleteMultiRange`, sample browser) or when the GMA `lock` is held (it can't re-enter the allocator).
- The service may run nested in another task's `yield()` (SD waits during song loading, with instruments half
  built). Fine: it touches only the GMA, its own tables and the registered `DeferredAlloc` objects, plus the
  construct/destroy callbacks, which only touch the new memory (placement-new of a `GrainBuffer`/
  `GranularProcessor`, zero-fill).
- Owner destroyed before completion: its `DeferredAlloc` destructor cancels. Queued -> slot freed. In progress
  (only possible if the GMA called back into code that destroyed the owner) -> slot marked CANCELLED with no owner,
  and the service frees the memory when the allocation returns instead of writing into the dead owner. Ready ->
  freed (destroy callback first; deferred in the audio routine).
- Re-request with a different size: queued -> slot resized in place (if in progress, the service notices on return
  and retries); ready -> old memory freed (deferred) and a new request queued. The Delay uses this when the rate
  changes before its buffer arrives.
- Stealables: the `GrainBuffer` is constructed by the allocating context immediately (STEALABLE region requires a
  construct callback) and starts `inUse`, so the GMA can never see an unconstructed or unclaimed Stealable. When the
  GMA steals it, `GrainBuffer::steal()` -> `GranularProcessor::grainBufferStolen()` -> `DeferredAlloc::forget()`;
  the next render re-requests it (deferred), and grain passes dry meanwhile.
- `DeferredAlloc` must not move while queued: it's neither copyable nor movable; owners (`ModFXProcessor`, `Delay`,
  `GranularProcessor`, `ModControllableAudio`) live in individually allocated objects and are never memmoved.
  `Delay`'s copy-assignment only copies config (unchanged).
- `Sound::voices_` growth: the service walks `AudioEngine::sounds` (Sounds remove themselves in their destructor) and
  calls `reserve()` on Sounds that asked; it only runs outside the audio routine, so no render holds an iterator.

## 4. Per-unit "not ready" behaviour

| unit | while pending | why |
|---|---|---|
| Mod FX delay-line types (chorus, stereo chorus, flanger, warble, dimension) | dry pass-through, no wet and no sqrt(2) post-FX level drop | the wet path is a comb on a zeroed delay line, so it fades in by itself when the buffer arrives; keeping the level unchanged avoids a step. The phaser needs no buffer and is unaffected |
| Grain (Sound or GlobalEffectable) | dry pass-through (the processor already did this when it had no buffer / it was stolen) | grain is wet-added to dry x dryVol; it needs half a buffer of history before grains start anyway (`bufferFull`) |
| Delay (start) | inactive, dry only | a starting delay outputs silence for the first delay time anyway; the pending time (ms) is far shorter than any delay time |
| Delay (rate change -> new secondary buffer) | keeps resampling the primary buffer | identical to the existing behaviour when allocation fails; the swap happens when the memory arrives |
| Delay / mod FX / stutter frees | memory released a few ms later by the task | inaudible |
| Stutter | unchanged (buffer set up from the UI task) | `beginStutter` doesn't run in the audio routine |
| Voices | not deferred: pools never touch the GMA under 48 concurrent objects; `voices_` capacity grown ahead | deferring a voice start would make the note late; see pools below |

Pools: `VoicePool`/`VoiceSamplePool`/`TimeStretcherPool` allocate in the tick only past 48 concurrent objects
(and free on recycle when the pool is full). Making that deferred would mean refusing the voice start (the note
stays pending through the existing `allowedToStartVoice()` path and starts late, ~2-10 ms) and refilling the pool
from the task. Not done: the test songs never exceed 48, and a late note is a worse artefact than a ~2 us
allocation. If wanted, the right fix is a bigger pre-population (or growing the pool capacity from the deferred task
when it runs low), not deferring the start.

## 5. Measurements

All lean, Deluge speed (`--plugin timectl=1,cachesim=0,cpi=1.45 --lean`, 60 slices = ~22 s of play), one emulator at a
time, `tools/run_emu_e4.py`; per-render data from `renders_all.bin` (whole run), analysed with `tools/e4_compare.py`
(first 20 s after PLAY; "start" = first 2 s, "steady" = 2..20 s). Render times are routine_() (tick + render) in
guest us. Ranges are min..max over the repeated runs. Run dirs: `/home/user/perfwork/runs/e4-*`.

| song | fw | runs | steady load % | steady culls/5 s | steady p99 render (us) | steady max render (us) | start p99 (us) | start max (us) | start culls |
|---|---|---|---|---|---|---|---|---|---|
| E4FX | base | 3 | 84.7..85.1 | 16.4..20.0 | 1117..1126 | 1687..1707 | 1035..1107 | 1387..1597 | 2..3 |
| E4FX | E4 | 4 | 84.8..85.2 | 14.4..19.4 | 1117..1127 | 1618..1759 | 945..1076 | 1376..1678 | 0..4 |
| E4RATE | base | 2 | 85.3..85.4 | 265.0..268.6 | 1527..1527 | 2842..2853 | 1356..1366 | 1749..1759 | 66..77 |
| E4RATE | E4 | 2 | 85.4..85.4 | 272.2..278.1 | 1528..1547 | 2943..2943 | 1367..1387 | 1879..1929 | 82..82 |
| FXVARY | base | 2 | 76.1..76.5 | 903.6..911.9 | 1935..1943 | 2329..2498 | 1904..1914 | 2339..2339 | 339..346 |
| FXVARY | E4 | 3 | 76.1..77.0 | 896.9..915.6 | 1924..1954 | 2247..2318 | 1796..1884 | 2107..2197 | 330..351 |
| GRAIN | base | 2 | 82.7..84.3 | 0 | 924..995 | 1347..1357 | 764..785 | 1347..1348 | 0 |
| GRAIN | E4 | 2 | 82.6..82.8 | 0 | 924..924 | 1336..1408 | 773..774 | 1217..1237 | 0..1 |
| TICKLOAD | base | 1 | 59.9 | 0 | 482 | 814 | 473 | 814 | 0 |
| TICKLOAD | E4 | 1 | 59.9 | 0 | 482 | 823 | 482 | 813 | 0 |

(GRAIN base run 1 had 84.3 % steady load, run 2 82.7 %: the first is an outlier, E4 matches run 2.)

E4FX in the renders right after each half-bar automation toggle (where main allocates/frees the kit mod FX and
SYN1/2 delay buffers; these renders also carry the beat's kit hits, so they're heavier anyway): mean 736..771 us
(base) vs 741..770 us (E4), culls 17..23 vs 19..21 per run. No difference.

Full mode (per-render instruction accounting), E4FX, 40 slices: `runs/e4-trace-base-E4FX-full` (base + trace) vs
`runs/e4-E4FX-full`: 5369 vs 5379 instructions per frame, 85 % load both, culls 15.4 vs 11.7 /5 s (noise level, see
the lean spread).

Deferred allocator counters (`deferredAllocStats`, E4 runs):

| song | requests queued = served | frees queued = served | failed / cancelled / table full / free-queue full | max queued | mean / max latency queue -> ready | sync allocs in audio routine | sync deallocs |
|---|---|---|---|---|---|---|---|
| E4FX | 45..58 (~2.4/s) | 36..46 | 0 / 0 / 0 / 0 | 4 | 73 / 140 samples (1.6 / 3.2 ms) | 19 | 0 |
| E4RATE | 223..226 (~10/s) | 211..214 | 0 / 0 / 0 / 0 | 6 | 79..83 / 168..180 samples (1.8 / 4.1 ms) | 19 | 0 |
| FXVARY | 9 | 0 | 0 | 4 | 75 / 106 samples | 21 | 0 |
| GRAIN | 1 | 0 | 0 | 1 | - | 15 | 0 |
| TICKLOAD | 0 | 0 | 0 | 0 | - | 15 | 0 |

**Allocation-free proof.** The remaining synchronous allocations (15-21, all
`ResizeableArray::insertAtIndex` first insertion = the arpeggiators' note arrays getting their first memory) all
happen in the first song tick: `stats/sNNNN.bin` dumps taken every ~5 s of play show the counter already final at
the first dump (t = 4.6-7.2 s) and unchanged to the end, while deferred requests and frees keep coming (E4FX:
22 -> 58 requests, 13 -> 46 frees between t = 7 s and 26 s). Synchronous deallocations: 0. So after the first
step, the tick and render make **no** GMA calls in any of the test songs.

### What this says

- **No measurable effect on steady-state load, render time or culls.** The GMA is fast in these sessions: ~1-2 us
  per alloc/dealloc (an almost empty region, no stealing), and even the churn song E4RATE spends ~1.8 ms of GMA time
  in ~23 s of play (0.008 % of the time). Base and E4 are inside each other's run-to-run spread in E4FX, FXVARY,
  GRAIN, TICKLOAD. E4RATE is ~3 % worse on culls and ~100 us on the single worst render in both E4 runs - plausibly
  real: while a new secondary delay buffer is pending (~2-4 ms), the delay keeps *resampling* its primary buffer, which
  costs more per sample than the native-rate path it would otherwise switch to; it's within 2 runs' noise, though.
- **Song start: the worst render gets ~110-230 us shorter** where main allocated big buffers in the first renders:
  GRAIN (song-level grain: 356 B + 512 KiB stealable in the first render) 1347 -> 1217..1237 us max, FXVARY 2339 ->
  2107..2197 us max and start p99 1904..1914 -> 1796..1884 us. Consistent over all runs, but it doesn't change culls
  (the codec buffer is never near empty at start; culls come from the direness prediction, see baseline.md).
- **Where E4 would really matter is not in these songs**: when the stealable region is full of sample clusters, an
  allocation in the render (512 KiB grain buffer, a 345 KiB delay buffer) goes through `CacheManager::ReclaimMemory`
  and steals/evicts clusters inside the render - unbounded time. The test songs are synth-only with an empty
  stealable region, so they can't show that. A sample-heavy song (or pre-filling the cache) would be the test.
- The tick's GMA calls were tiny (the arp arrays' failing `extend()` probe, ~30/s, sub-microsecond) - E4 removes
  them, but they were never a cause of tick cost.

## 6. Risks

- **Effects start a few ms late** (here mean ~1.7 ms, max 4.1 ms at 85 % load): mod FX/grain/delay are dry for that
  long when they switch on in the render (song start, clip launch, kit mod FX depth/feedback/offset leaving min,
  delay feedback rising from 0, a sound starting after its delay tail was discarded). Inaudible for delay (silent
  for the first delay time anyway) and grain (needs half a buffer before grains start); for chorus/flanger the
  wet signal starts ~2 ms later. Under heavy UI/SD load the low-priority task can wait longer (max interval 10 ms,
  and routineWithClusterLoading also services); there's no hard bound.
- **Delay rate changes** take effect a few renders later, with the primary resampling meanwhile (more CPU, see
  E4RATE).
- **Memory is held a bit longer** (deferred frees, ready-but-unclaimed buffers) and the stealable `GrainBuffer` is
  now constructed by the service; a bug in the destroy/detach ordering would be a use-after-free inside the GMA's
  steal path. The handle protocol depends on "no ISR touches the GMA" and on owners never memmoving a registered
  `DeferredAlloc` (enforced only by deleted copy/move and review).
- **Starvation / table full**: 64 request slots and 64 queued frees; overflow fails soft (request retried next
  render; a free falls back to synchronous). Never hit in the tests (max 6 queued).
- **`Sound::voices_` reserve(4)** per Sound: 32 B (+8 B header) in internal RAM for every Sound, played or not
  (kits with many rows). Growth past that is now done from the deferred task when within 2 of capacity; a burst
  can still realloc in the tick (counted).
- Not covered: pools beyond 48 voices/voice samples/time stretchers, time-stretch buffers, perc caches, live pitch
  shifters, DX engine, first arp-array allocation (one-time). All still allocate synchronously in the audio routine
  (and are counted, so a debug build can show them).
- The `audioRoutineDepth` checks add one load+branch to every GMA call (negligible); the counters are always on in
  this branch - upstream would put them under `ALPHA_OR_BETA_VERSION`.

## 7. Would I propose it upstream?

Not as a performance fix: it doesn't move load, render time or culls in any of the songs here, and it adds ~600 lines
plus a new concurrency protocol to the allocator. The tick/render cost problem the investigation is about is the
direness prediction and voice-start policy (baseline.md), not allocation.

As a robustness change, a trimmed version is worth proposing: the render's big allocations (grain buffer 512 KiB,
delay buffers up to 700 KiB, GlobalEffectable mod FX on every on/off toggle) can steal sample clusters inside the
render, which is unbounded and can stall audio in sample-heavy sets. I'd propose: the `DeferredAlloc` + service task
+ `deallocDeferred` (commit 1, counters under `ALPHA_OR_BETA_VERSION`), mod FX/grain (commit 2) and delay
(commit 3) - but first measure a sample-heavy song with a full stealable region to show the stall it removes. The
`ResizeableArray` / `voices_` tick changes (commit 4) aren't worth it on their own.

## Commands

```sh
export PATH=/home/user/perfwork/bin:$PATH; cd /home/user/perfwork
# allocation inventory on main (trace firmware):
python3 tools/run_emu_e4.py --elf /home/user/perfwork/elf/e4-base-trace-fx.elf --sd /home/user/perfwork/sd_e4.img \
    --out runs/e4-trace-base-fx --plugin timectl=1,cachesim=0,cpi=1.45 --lean --slices 60
python3 tools/alloc_trace.py runs/e4-trace-base-fx elf/e4-base-trace-fx.elf
# A/B:
python3 tools/run_emu_e4.py --elf /home/user/perfwork/elf/e4-probe2-E4FX.elf --sd /home/user/perfwork/sd_e4b.img \
    --out runs/e4-E4FX-leanN --plugin timectl=1,cachesim=0,cpi=1.45 --lean --slices 60
python3 tools/e4_compare.py runs/e4-base-E4FX-lean{1,2,3} runs/e4-E4FX-lean{1,2,3,4} --secs 20
```
Queue scripts: `runs/e4-queue{1,2,3,4}.sh` (logs alongside). The run of queue 3 + a probe build were interrupted by a
container restart; queue 3 had completed, the build was redone.
