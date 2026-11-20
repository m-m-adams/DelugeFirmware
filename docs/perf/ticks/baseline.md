# Phase 2: baseline on main (probe e339b27, main 680b93c)

All numbers are from the emulator at Deluge speed (`timectl=1,cachesim=0,cpi=1.45`). The firmware is unchanged:
`/home/user/perfwork/elf/probe-base.elf` with only `dzSongPath` patched. Probe "cycles" are QEMU PMU cycles, which
are **nanoseconds of guest time**: 1 sample = 22,676 ns, and 1 sample of time is about 6,255 instructions at
cpi 1.45 / 400 MHz. The plugin's `SCOPE cyc=` values are A9 cycles (400 MHz), and `ins=` are exact instruction counts.

## TL;DR

- **Benchmarks** (card `/home/user/perfwork/sd-baseline.img`, 20 voices per 16th step):
  - **BU6** (edge): 5 synth clips x 4-note chords, unison 6. Load 87%, **11 culls/5 s, mostly soft** (range 7.3-15.9
    over 4 runs).
  - **BU8** (heavy): the same with unison 8. Load 87%, **191 culls/5 s, 40% hard events**, reproducible to 0.3%.
  - **KIT20** (tick-dominated, light): 1 kit with 20 synth rows. Load 69%, **1.2 culls/5 s**, step tick 29 samples.
  - **BKIT20U4** (tick-dominated, heavy): KIT20 with unison 4. Load 86%, **55 culls/5 s**, step tick 27 samples.
- **Culls do line up with ticks**, but the tick's own execution time isn't what crosses the line. In BU6, KIT20 and
  BKIT20U4, 100% of culls happen in the render that starts with a tick, or in the 1-2 renders after it. The causes are
  in the **prediction** that setDireness() makes:
  1. songTickCost (11 samples for the synth songs, 27-29 for the kits) is reserved in every render's margin,
     tick or not.
  2. The margin is computed for the *doubled* render size from getRenderSize(), not the size the render needs.
  3. The render before a tick is cut short at the tick, so its predicted size is wrong. That primes cullVoices()'s
     "rising over-limit" soft-cull rule.

  The codec buffer itself never came near empty: the least queued at a render start was 72-84 samples. A static
  replay of cullVoices() gives these results:
  - Reserving songTickCost only in renders that action a tick: BU6 8.1 -> 0.7 cull events/5 s, KIT20 1.2 -> 0,
    BKIT20U4 50.7 -> 7.2.
  - Also judging overload on the minimum render size: 0 / 0 / 1.5.
  - BU8 still keeps 2-20 events/5 s. It's genuinely render-bound once the full chord has started.
- **Pending voice starts are not a cull cause.** The 1-20 renders after a step never cull. They're the cheapest
  renders in the song, because the old voices are gone and the new ones trickle in.
- **cpuDireness is pinned at 14 (confirmed).** It's 14 in 100% of renders at every load of 70% or more, and above 12
  in 91% of renders at 60-62% load. It's 0 at 44% load. The result is 1 voice start per render, so a 20-note chord
  takes 19 extra renders to sound completely: **10-18 ms of chord start latency on every step** (1 ms at 44% load).
  Direness 14 also switches the oscillators to crude saws, sample interpolation to linear, and the ladder filter's
  oversampling off.
- **Where tick time goes**, per 16th step:
  - BU6: 86.7k instructions (13.9 samples). Note-ons 32% (19 of the 20 are *refused* and still cost about 1.1k
    instructions each, mostly Arpeggiator::noteOn bookkeeping with the arp off), note-offs 21%, arp tick-forward and
    scheduling 21%, row scanning 17%, clip overhead 6%.
  - Kit: 204k instructions (32.5 samples), and about 2/3 of it is kit-level per-note overhead in
    Kit::noteOnPreKitArp / noteOffPreKitArp (about 3.7k / 3.1k instructions per note on top of Sound::noteOn/noteOff:
    a linear getNoteRowForDrum plus the kit arpeggiator, which runs even with the arp off).
  - A voice start (Sound::noteOnPostArpeggiator) is 5.5k instructions (0.88 samples). 58% of it is
    Patcher::performInitialPatching.

## Benchmark songs and exact commands

Songs (generator `/home/user/perfwork/song/gen_tickload.py`) are in `/home/user/perfwork/sdcard-baseline/SONGS/`,
alongside copies of the original 6 songs. The card image is `/home/user/perfwork/sd-baseline.img`. Treat it as
read-only and copy it if you rebuild it. The shared `sdcard/` and `sd.img` were not touched.

| name | file | generator | probe key (`set_song.py`) | ELF (ready made) |
|---|---|---|---|---|
| BU6 | TICKLOAD_BU6.XML | `--unison 6` | `BU6` | `/home/user/perfwork/elf/base/probe-BU6.elf` (md5 ca0df6fc) |
| BU8 | TICKLOAD_BU8.XML | `--unison 8` | `BU8` | `/home/user/perfwork/elf/base/probe-BU8.elf` |
| KIT20 | TICKLOAD_KIT20.XML (original) | `--kit 20` | **`D_KIT20`** ("KIT20" also matches BKIT20U4, which sorts first) | `/home/user/perfwork/elf/base/probe-KIT20.elf` |
| BKIT20U4 | TICKLOAD_BKIT20U4.XML | `--kit 20 --unison 4` | `BKIT20U4` | `/home/user/perfwork/elf/base/probe-BKIT20U4.elf` |

Ladder songs also on the card: BU5 (`--unison 5`), BU7 (`--unison 7`), BU4V24 / BU4V28 (`--unison 4 --voices 24|28`).
Everything else is the generator default: 120 BPM, 1 bar, 5 clips, gate 1.0, release 0, no FX, no arp.
md5 of the XMLs: BU6 6219e1f9, BU8 e3826440, KIT20 899cd0c1, BKIT20U4 92ec2e8d.

Reproducing the baseline on an experiment ELF:

```sh
export PATH=/home/user/perfwork/bin:$PATH
cp <your probe build>.elf /home/user/perfwork/elf/<exp>-BU6.elf
python3 /home/user/perfwork/tools/set_song.py /home/user/perfwork/elf/<exp>-BU6.elf BU6     # or BU8, D_KIT20, BKIT20U4
/home/user/perfwork/tools/run_emu_rings.py --elf /home/user/perfwork/elf/<exp>-BU6.elf \
    --sd /home/user/perfwork/sd-baseline.img --out /home/user/perfwork/runs/<exp>-BU6-lean1 \
    --plugin timectl=1,cachesim=0,cpi=1.45 --lean            # 200 slices, about 2.5 min wall when the host is quiet
T=/home/user/perfwork/tools
$T/summarize.py runs/base-BU6-lean1 runs/<exp>-BU6-lean1    # load, culls/5 s, voices, tick table
$T/cullattr.py  runs/<exp>-BU6-lean1                        # per-bucket tick/render table, cull attribution
$T/cullsim.py   runs/<exp>-BU6-lean1                        # cull replay, least queued, chord start latency
# Full mode (scopes): drop --lean; --slices 110 is enough (SCOPE totals start 5e9 instructions after PLAY)
# Custom scopes:      --scopes 'Sound::noteOn(|Sound::noteOff(|...'    (6 demangled-name prefixes, '|'-separated)
```

New tools (all in `/home/user/perfwork/tools/`; delugemu untouched):
- `run_emu_rings.py`: run_emu.py with two additions. (1) It stops the VM and dumps the dzRenders ring on every
  10 s poll, then merges the dumps into `<out>/renders_all.bin`. This holds **every** render of the run, about 85k,
  where the ring alone holds the last 16384 (about 8 s); there were 0 gaps in all runs. Stopping the VM is harmless
  under time control. (2) `--scopes` takes custom plugin scope functions (full mode) and writes `<out>/scopes.txt`.
- `cullattr.py`: tick vs render by "renders since the step tick" (the step tick is the tick that starts voices), cull
  shares by bucket (step tick and any tick), queue and margin after a step tick, and predicted vs actual render.
- `cullsim.py`: replays cullVoices() on the ring under other margin rules (see "Cull attribution"). Agreement with
  the recorded culls was 1.0000 on every run. It also reports the least queued at a render start, the least time
  left when a render finished, and the chord start latency.

## 1. Load ladder

From `runs/base-ladder.txt`, after skipping 25 s (0 s for ring-only runs). "stepTick" is the mean/max cost of the
tick that starts the step, in samples. "other" is the other ticks, mean in samples. tCost is songTickCost.
"lat" is the chord start latency. minQ is the least queued at a render start, in samples.

| run | load | culls/5 s | hard/soft events per 5 s | voices | stepTick | other | tCost | direness>12 | lat ms | minQ | ticks/s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| N10 | 44% | 0 | 0/0 | 10.1 | 7.6/8.0 | 0.4 | 7.9 | 0% | 1.0 | 109 | 16 |
| TICKLOAD (probe-smoke) | 60% | 0 | 0/0 | 19.6 | 10.8/13.3 | 0.8 | 12.9 | 91% | 7.9 | 99 | 23 |
| FX (D_FX song) | 62% | 0 | 0/0 | 19.6 | 11.0/13.3 | 0.8 | 12.8 | 91% | 8.3 | 98 | 23 |
| N30 | 70% | 0 | 0/0 | 28.0 | 17.0/17.7 | 1.0 | 17.7 | 100% | 15.0 | 88 | 32 |
| U4 | 83% | 0 | 0/0 | 19.1 | 10.8/11.1 | 1.0 | 11.0 | 100% | 10.8 | 94 | 32 |
| BU5 | 86% | 0 | 0/0 | 19.1 | 10.6/11.0 | 1.0 | 11.0 | 100% | 12.1 | 90 | 32 |
| **BU6** (4 runs) | 87% | **8.5 / 15.9 / 12.9 / 7.3** | 0.1-0.8 / 7-14 | 18.7 | 10.9/11.5 | 1.0 | 11.0-11.4 | 100% | 13.1 | 84 | 32 |
| BU7 | 88% | 87.9 | 11.8/64.3 | 17.4 | 10.8/11.1 | 1.0 | 11.0 | 100% | 13.8 | 80 | 32 |
| **BU8** (2 runs) | 87% | **191.7 / 191.1** | 75/42 | 14.9 | 11.0/11.5 | 1.1 | 11.4 | 100% | 14.1 | 73 | 32 |
| BU4V24 | 86% | 6.6 | 0/6.6 | 22.3 | 12.7/13.3 | 1.0 | 13.2 | 100% | 14.5 | 85 | 32 |
| BU4V28 | 87% | 124.5 | 31/62 | 23.6 | 14.8/15.5 | 1.1 | 15.5 | 100% | 18.2 | 71 | 40 |
| **KIT20** (2 runs) | 69% | **1.2 / 1.2** | 0/1.2 | 19.3 | 28.8/29.6 | 1.2 | 29.3 | 100% | 10.5 | 78 | 32 |
| **BKIT20U4** (2 runs) | 86% | **57.9 / 52.6** | 4-7/44 | 18.0 | 26.5/27.4 | 1.3 | 27.0 | 100% | 13.2 | 72 | 32 |

Notes:
- Run dirs: `/home/user/perfwork/runs/base-<song>-lean{1,2}`, plus `base-BU6-hot` (lean + hotblocks: the same timing
  model) and `base-BU6-full` (full mode).
- Load saturates at about 87% while culls rise 0 -> 8 -> 88 -> 192 per 5 s from BU5 to BU8. The firmware culls to
  hold the load there.
- **Tick rate 23 -> 32 -> 40 per second has nothing to do with FX** in these songs: the D_FX song on my card ran at
  23/s, the same as TICKLOAD. It comes from **pending notes**. ArpeggiatorBase::doTickForward() returns 0 ticks to the
  next event while handlePendingNotes() has a note pending (arpeggiator.cpp:1573), even with the arp off. So while a
  chord trickles in at 1 voice per render, the session schedules a tick on every song tick (+1, +2, +3 ticks = 230,
  460 and 690 samples after the step). Each of those ticks cuts the render before it short. 2 ticks per step at 44%
  load (fast starts), 3 at 60%, 4 at 70-88%, 5 for 28-note chords. The coordinator's FXALL/FXVARY tick rates may be
  this effect (more load -> longer pending) as well as synced FX. Worth checking before blaming the FX.

## 2. Tick vs render, and cull attribution

### Per-render picture (BU6 run lean2; BU6 lean1/full/hot have the same shape)

dStep = renders since the step tick (0 = the render whose window starts with the step tick; its culls happen in
setDireness() *before* the tick runs). Cycles are ns. Samples are rendered samples. sT/sP = voices started in the
tick / from pending. Margin is setDireness()'s predicted margin.

| dStep | renders | total ns | tick ns | render ns | samples | ns/sample | sT | sP | margin | queued | voices at end | culls (hard/soft) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 (step tick) | 385 | 1,375,800 | 249,849 | 1,120,930 | 60.3 | 18,598 | 1.00 | 0 | 36.4 | 95.3 | 20.8 | 98 (8/82 events) |
| 1 | 385 | 1,373,957 | 0 | 1,371,513 | 71.1 | 19,277 | 0 | 1.00 | 21.5 | 89.8 | 9.2 | 49 (0/49) |
| 2 | 385 | 596,363 | 0 | 594,466 | 59.4 | 10,005 | 0 | 1.00 | 36.0 | 95.8 | 2.9 | 0 |
| 3 | 385 | 175,743 | 0 | 175,170 | 25.0 | 7,021 | 0 | 1.00 | 68.7 | 112.3 | 3.9 | 0 |
| 4 | 385 | 154,021 | 9,624 | 144,189 | 12.1 | 11,946 | 0 | 1.00 | 84.9 | 118.4 | 4.9 | 0 |
| 5 | 385 | 207,479 | 11,937 | 195,256 | 19.0 | 10,301 | 0 | 1.00 | 82.7 | 115.1 | 5.9 | 0 |
| 6-10 | 1925 | 285,844 | 1,920 | 283,800 | 20.3 | 13,991 | 0 | 1.00 | 85.9 | 115.2 | 8.9 | 0 |
| 11+ | 48650 | 821,643 | 318 | 819,565 | 40.8 | 20,082 | 0 | 0.07 | 58.1 | 104.9 | 19.6 | 6 (0/6) |

How a step plays out (render lists: `ticks.py runs/base-BU6-lean1/renders_all.bin --list 12`):
- The render before the step is **cut short at the tick**, to 16-23 samples. setDireness() predicted it at the doubled
  size, so its margin reads 43-49 (already "over" the soft limit of 48).
- The **step-tick render** has a tick of 10.8 samples. It starts 1 voice and refuses 19. The notes-off of the previous
  chord happen in the same tick (gate 1.0), so the render renders 20 fast-releasing voices plus 1 new one:
  60 samples in about 50 samples of time.
- **dStep 1** is the biggest render of the step: 71-80 samples, because getRenderSize() doubles into the space the
  step render used up. The predicted margin is 15-21. That's below the soft limit of 28 (48 - 20 x 1 started), and
  rising, so it **soft-culls**.
- **dStep 2-20**: the old voices are gone and the new chord starts 1 voice per render. These renders are small
  (12-25 samples, 7-14k ns/sample) and never cull.
- About 20 renders (13 ms) later the chord is complete and renders are back to 40 samples at 20k ns/sample (88%).

Tick vs render, step-tick render (all four benchmarks):

| | BU6 | BU8 | KIT20 | BKIT20U4 |
|---|---|---|---|---|
| step tick, mean (p50/p99/max), samples | 11.0 (11.0/11.5/11.5) | 11.0 (11.0/11.5/11.5) | 28.8 (28.7/29.2/29.6) | 26.7 (26.5/26.9/27.4) |
| other ticks (pending), mean, samples | 1.0 | 1.1 | 1.2 | 1.3 |
| step-tick render: render part / total, samples | 49.4 / 60.7 | 45.8 / 57.0 | 20.5 / 49.5 | 38.5 / 65.4 |
| tick share of the step-tick render's time | 18% | 19% | 58% | 41% |
| tick share of all cycles (summarize) | 0.3% | 0.3% | 0.9% | 0.6% |
| render ns/sample: step render / dStep 1 / dStep 2-10 / steady | 18.6k / 19.3k / 7-14k / 20.1k | 18.0k / 18.7k / 8-15k / 20.0k | 17.0k / 12.6k / 11-17k / 15.5k | 18.2k / 17.5k / 7-15k / 19.5k |
| voice starts per render | 1 in the tick, then 1 per render for ~19 renders (pending) | same | same (19 refused, 57 refusals counted incl. retries) | same |
| songTickCost reserved in every render | 11.4 | 11.4 | 29.3 | 27.2 |
| predicted margin p1 / p50 | 25 / 64 | 10 / 62 | 53 / 64 | 9 / 54 |
| direness | 14 always | 14 always | 14 always | 14 always |

### Where culls happen

Culls by renders since the **step** tick, then since **any** tick (the cheap pending ticks included). "0" is the
render that starts with the tick; its cull is decided before the tick runs.

| run | culls | step: 0 / 1 / 2 / 3-10 / 11+ | any tick: 0 / 1 / 2 / 3+ | hard events in tick bucket |
|---|---|---|---|---|
| BU6 lean1 | 84 | 39% / 52% / 0 / 0 / 8% | 44% / 56% / 0 / 0 | 2 of 4 hard at step d0 |
| BU6 lean2 | 153 | 64% / 32% / 0 / 0 / 4% | 65% / 35% / 0 / 0 | 8 of 8 at step d0 |
| BU6 full / hot | 72 / 125 | 29%/71% and 66%/34% at d0/d1 of any tick | 100% within 1 render of a tick | |
| BU8 lean1 | 1923 | 5% / 3% / 0 / 0 / **92%** | 34% / 32% / 20% / 14% | 732 of 755 hard events are in step 11+ |
| KIT20 | 12 | 0 / **100%** / 0 / 0 / 0 | 0 / 100% | none hard |
| BKIT20U4 | 556 | 25% / 26% / 15% / 0 / 34% | 54% / 31% / 15% / 0 | 40 of 69 hard at step d0 |

Timing of BU8's culls: 66% happen 600-1000 samples after the step. That's just after the last pending tick (+690),
when the last notes of the chord have started and all 20 x 8-unison voices render at once.

### What causes them: the cull replay (`cullsim.py`)

The replay feeds each render's recorded queue and prediction to cullVoices() under a different margin rule. It's
static: it doesn't model the extra voices (and render cost) that fewer culls would leave playing. Events per 5 s:

| rule | BU6 | BU7 | BU8 | BU4V24 | BU4V28 | KIT20 | BKIT20U4 |
|---|---|---|---|---|---|---|---|
| as is (matches recorded 100%) | 8.1 | 76.1 | 116.4 | 6.6 | 93.4 | 1.2 | 50.7 |
| no songTickCost reservation | 0.6 | 6.5 | 52.8 | 0.0 | 5.4 | 0 | 0.0 |
| songTickCost only in renders that action a tick (E1) | 0.7 | 15.5 | 63.8 | 0.5 | 30.7 | 0 | 7.2 |
| margin for the minimum render (space), not the doubled size | 0.0 | 0.5 | 20.6 | 0.0 | 3.3 | 0 | 24.5 |
| tick-only reservation + minimum size | **0.0** | 0.2 | 2.7 | 0.0 | 0.4 | 0 | **1.5** |

For the minimum-size rule, the predicted render is scaled down by the fitted per-sample cost (0.66-0.77 samples of
time per sample, plus a fixed 5-10 samples per render), so the fixed cost is kept.

How close audio actually came to an underrun:
- The least queued at any render start was 84 (BU6), 73 (BU8), 78 (KIT20) and 72 (BKIT20U4) samples. Not a single
  render started with less than 16 queued.
- The least time left when a render finished (queued at start minus the render's total time) was 13 (BU6), 4.7 (BU8),
  43 (KIT20) and 8.8 (BKIT20U4) samples. Those minima are in the big catch-up renders that getRenderSize() chose to
  make.

Answer to "tick itself, pending starts, reservation, or steady load?":
- **The tick's execution** costs 18-58% of the step-tick render's time. It drains the queue, so the next render
  (dStep 1) has the least margin of the step: BU6 queued 95 -> 90, KIT20 112 -> 83, BKIT20U4 102 -> 78. That's the
  real cost of the tick. But it's never enough to endanger the codec. It only shows up as a cull because of the
  prediction.
- **Pending voice starts**: not a cause. dStep 2-20 has no culls in any run.
- **songTickCost reservation**: the biggest single factor in BU6, KIT20 and BKIT20U4. It's 11-29 samples taken off
  every render's margin, and a 0.4-1.3-sample pending tick gets the same reservation as the 29-sample step tick.
  Removing it in non-tick renders removes 90-100% of their culls.
- **The doubled render size and the tick truncation** put the other 20-60 samples of "phantom" load in the
  prediction. getRenderSize() lets a render shrink the margin down to kRenderSafetyMargin (8). cullVoices()
  soft-culls at a margin of 48 (28 with a start in the previous render). **The two disagree by design.** So whenever
  the load is above about 50% (where doubling diverges until it hits the cap), the margin regularly drops below the
  soft-cull limit. The soft rule's "rising over-limit" test is satisfied right at the tick boundary: the truncated
  pre-tick render reads over-limit, then the big step/catch-up render reads more over-limit.
- **Steady render load**: only in BU8 (and partly BU7/BU4V28). With all voices playing, the render runs at 88-100% of
  real time, and culls remain under every rule (2-20/5 s).

## 3. Where tick time goes

Data: full mode BU6 with the standard tick scopes (`runs/base-BU6-full`, 453 steps). Custom-scope full runs:
`runs/base-BU6-full-scopesB` (Sound::noteOn, Sound::noteOff, reassessRenderSkippingStatus, Arpeggiator::noteOn,
Patcher::performInitialPatching, Session::doTickForward) and `runs/base-BKIT20U4-full` / `-full-scopesB`
(Kit::noteOnPreKitArp, Kit::noteOffPreKitArp, Sound::noteOn, Sound::noteOff, Kit::doTickForwardForArp,
Session::doTickForward). Instructions are exact (plugin). One step = 4 ticks: the step tick, which is about 78% of
the tick work, plus 3 pending ticks.

Per-call plugin SCOPE costs, BU6:

| scope | calls per step | ins per call | per step |
|---|---|---|---|
| PlaybackHandler::actionSwungTick | 4 | 21.7k (avg) | 86.7k |
| Session::doTickForward | 4 | 21.1k | 84.3k |
| InstrumentClip::processCurrentPos | 20 (5 clips x 4) | 3.29k | 65.8k |
| NoteRow::processCurrentPos | 20 | 1.66k | 33.2k |
| Sound::noteOn (from the clip's pending-note-on list) | 20 | 1.38k | 27.5k |
| Arpeggiator::noteOn (inside Sound::noteOn, arp OFF) | 20 | 0.81k | 16.3k |
| Sound::noteOff (inside NoteRow::processCurrentPos) | 20 | 0.91k | 18.3k |
| Sound::noteOnPostArpeggiator (voice start: 1 in tick, 19 from pending in renders) | 20 | 5.50k | (110k, 104k of it in renders) |
| Voice::noteOn | 20 | 4.68k | |
| Patcher::performInitialPatching (inside Voice::noteOn) | 20 | 3.21k | |
| Sound::process_postarp_notes (every Sound every render) | 708 | 167 | 118k (incl. the 19 starts) |

**BU6 tick breakdown, per step: 86.7k instructions = 13.9 samples. The step tick alone is about 67.6k = 10.8
samples.**

| part | how computed | ins/step | samples | share |
|---|---|---|---|---|
| note-on: 1 voice actually started | noteOnPostArp 5.5k + Sound::noteOn overhead | ~6.9k | 1.1 | 8% |
| note-on: 19 refused starts (arp noteOn bookkeeping, render-skip reassess) | Sound::noteOn - the above | ~20.6k | 3.3 | 24% |
| note-offs + release start | Sound::noteOff | 18.3k | 2.9 | 21% |
| note-row scanning | NoteRow::processCurrentPos - noteOff | 14.9k | 2.4 | 17% |
| clip-level overhead | InstrumentClip - rows - noteOns | 5.1k | 0.8 | 6% |
| arp tick-forward + scheduling (Session minus clips; handlePendingNotes per instrument per tick) | doTickForward - InstrumentClip | 18.5k | 3.0 | 21% |
| playback handler (considerLaunchEvent, swung tick scheduling) | actionSwungTick - doTickForward | 2.4k | 0.4 | 3% |
| param automation | none in the song (AutoParam/ParamManager under 0.01% of instructions) | ~0 | 0 | 0% |

**Kit (BKIT20U4, the same tick as KIT20), per step: 204k instructions = 32.5 samples. The step tick is about 165k
= 26.7 samples.**

| part | ins/step | share |
|---|---|---|
| note-on path: Kit::noteOnPreKitArp (20 x 4.34k), of which Sound::noteOn is only 20 x 0.67k | 86.7k | 43% |
| note-off path: Kit::noteOffPreKitArp (20 x 3.45k), of which Sound::noteOff is only 20 x 0.36k | 69.0k | 34% |
| note-row scanning (NoteRow::processCurrentPos 20 x 4.21k, minus note-offs) | 15.2k | 7% |
| Kit::doTickForwardForArp (4 x 6.85k) | 27.4k | 13% |
| other | ~5k | 3% |

The kit-level overhead per note is about 3.7k instructions (on) and 3.1k (off), on top of Sound::noteOn/noteOff.
That's **about 136k instructions = 22 samples per step, 2/3 of the kit tick**. Kit::noteOnPreKitArp /
noteOffPreKitArp (kit.cpp:1289) do three things for every note:
- `getNoteRowForDrum()`, a linear search over the rows, so the step is O(rows^2).
- `allowNoteTails()`.
- The kit arpeggiator's noteOn/noteOff, with 20 held notes. This runs whenever `arpSettings != nullptr`, even with
  the kit arp mode OFF.

The flat profile (`runs/base-BU6-hot/bucket.txt`: hotblocks minus `base-BU6-toplay`) agrees. The tick is about 0.3%
of all instructions: NoteRow::processCurrentPos self 0.01%, Voice::noteOn 0.05%, Patcher::performInitialPatching
0.13%. The render dominates: renderOsc 28%, LpLadder 15%, **renderCrudeSawWaveWithAmplitude 12.7%** (the
direness-14 path), renderBasicSource 9.5%, Sound::render 8.5%. Reverb Mutable::process is 4.8% even though no sound
sends to it. The problem is concentration in time, not total cost.

**A voice's start vs steady render.** The start itself is 5.5k instructions (0.88 samples): Voice::noteOn 4.68k, of
which performInitialPatching 3.21k. A voice's steady render costs about 1.4 samples (about 9k instructions) per 40-sample render in BU6
((20.1k ns/sample x 40 = 35.5 samples, minus about 7 samples fixed) / 20 voices). So a start costs about 60% of one
steady voice-render. I couldn't separate a voice's *first* render from its later ones:
- Lean-mode PMU quantisation is 10 µs.
- The regression on the ring (cycles ~ samples + voices + starts) is confounded: R^2 0.88, and the start coefficient
  is unstable in sign.
- `tools/perf/flame.py` (dzstack) produces no call stacks on this host, because QEMU was built without capstone
  (`CONFIG_CAPSTONE` undefined, so `qemu_plugin_insn_disas` can't find BL). Rebuilding QEMU with libcapstone-dev
  would enable exact call-stack profiles. I didn't do it: it's the shared emulator binary.

## 4. cpuDireness: confirmed pinned at 14, with numbers

| song / load | direness histogram (renders) | margin p1 / p50 |
|---|---|---|
| N10, 44% | 0: 100% | 94 / 104 |
| TICKLOAD, 60% | 12: 13%, 13: 34%, 14: 53% | 80 / 90 |
| D_FX, 62% | 12: 9%, 13: 25%, 14: 65% | 78 / 87 |
| every song at 69% or more (N30, U4, BU5-8, KIT20, BKIT20U4) | **14: 100%** | 9-61 / 54-82 |

Why (audio_engine.cpp, setDireness()):
- `scaled = numSamplesLimit + kCullMargin - margin = 128 - margin`.
- Direness is raised when `scaled >= direnessThreshold (50)`, i.e. margin <= 78, to `min(scaled - 49, 14)`, i.e.
  `min(79 - margin, 14)`. **margin <= 66 gives direness > 12, and margin <= 65 gives 14.**
- It only falls 1 step per 1/8 s (5512 samples), and only while `scaled < 47`, i.e. margin > 81. Any render with
  margin <= 65 resets the timer.
- The margin is `queued - songTickCost - predict(getRenderSize(space))`. songTickCost alone takes 11-29 samples off.
  getRenderSize()'s doubling grows renders until the prediction nearly fills what's queued (the cap is
  `queued - 8 - songTickCost`), because doubling diverges at any load above 50%.
- So at 60-70% load the margin dips below 65 at least once per step (8 steps/s: about once per 1/8 s, exactly the
  decay interval), and direness never comes down. At 44% the renders stay small, the margin stays above 94 and
  direness is 0.

What direness gates (grep cpuDireness):
1. `allowedToStartVoice()`: `threshold = cpuDireness > 12 ? 1 : 4` voices per render. Combined with the tick
   resetting `voices_started_this_render`, this gives 1 start in the tick and 1 per render after it. **Chord start
   latency is 10-18 ms per step** (cullsim): BU6 13.1 ms over 19 renders, N30 15 ms over 29 renders, BU4V28 18 ms.
   It's 1 ms at 44% load (4 per render). Pending notes start with `ticksLate = samplesLate = 0`, so they lose
   sample-accurate timing too. The probe's 0.88-sample voice start means 4 starts per render would cost only about
   2.6 extra samples.
2. Oscillators (oscillator.cpp):
   - saw / square / supersaw use the crude, non-band-limited render for wave table numbers below `cpuDireness + 6`;
   - analog saw becomes the digital saw for tables 8..cpuDireness+5;
   - triangle takes the cheap path at >= 7.
   At 14 this covers most of the song's notes: renderCrudeSawWaveWithAmplitude is 12.7% of all instructions.
3. Sample interpolation (sample_controls.cpp): linear interpolation from octave `26 - direness/4` up. At 14 that's
   from 2 octaves below unstretched pitch.
4. Ladder filter (lpladder.cpp:69) oversampling, and hot-ladder drive oversampling (filter_set.cpp:153), are off at
   direness 14.
5. cullVoices() runs only under the same condition (`scaled >= 50`). That's why `last_num_samples_over` keeps state
   across renders and gets primed by the truncated pre-tick render.

**Consequence for the experiments:** anything that lowers direness, such as E1, will *raise* render cost per voice:
band-limited oscillators, cubic interpolation and filter oversampling come back. So instructions per frame and load
can rise while culls fall, and the audio quality changes. Compare culls, voices and chord latency, and report the
direness histogram (`cullattr.py` prints it).

## 5. Noise

Repeat runs (lean unless noted, 200 slices, after a 25 s skip):

| song | runs | culls/5 s | load | voices |
|---|---|---|---|---|
| BU6 | lean1, lean2, hot (lean+hotblocks), full | 8.5, 15.9, 12.9, 7.3: mean 11.2, range -35%/+42% | 87-88% | 18.7 all |
| BU8 | lean1, lean2 | 191.7, 191.1 (0.3%) | 87-88% | 14.9 both |
| KIT20 | lean1, lean2 | 1.2, 1.2 (12 culls each) | 69% | 19.3 |
| BKIT20U4 | lean1, lean2 | 57.9, 52.6 (10%) | 86% | 18.0 |

The tick and render costs are stable to about 1%: step tick 10.8-11.0 samples, scopes per call within 2% across
modes, songTickCost 11.0-11.4. BU6 is right at the edge (one soft cull per 5 steps), so its cull count is noisy.
Use 3+ runs, or `--slices 400`, and treat a change of less than ±40% as noise there. BU8, KIT20 and BKIT20U4 are
precise enough for single A/B runs at ±15%. Good secondary metrics: cull *events* split hard/soft, margin p1 and
step-tick samples (`cullattr.py`), and the cullsim chord latency.

## Recommendations for E1 / E2 / E3

**E1 (tick-aware prediction). The biggest win, and cheap.**
1. In setDireness(), add songTickCost only if a tick is due at the start of this window. tickSongFinalizeWindows()
   already computes `timeTilNextTick` from `timeNextTimerTickBig` / `scheduledSwungTickTime`, so factor it out and
   call it before setDireness(). The replay gives BU6 8.1 -> 0.7, KIT20 1.2 -> 0, BKIT20U4 50.7 -> 7.2, BU8
   116 -> 64 cull events/5 s.
2. Predict the render size the window will really have: `min(getRenderSize(space), timeTilNextTick)` when a tick
   cuts it short. This fixes the over-limit read in the truncated pre-tick render that primes the soft cull.
3. Judge overload on the **minimum** render (space samples), not the doubled size. getRenderSize() already protects
   the codec with kRenderSafetyMargin. With (1), this takes BU6/KIT20/BKIT20U4 to 0/0/1.5 and BU8 to 2.7. Or bring
   kCullMargin and kRenderSafetyMargin into agreement.
4. songTickCost is the max over 2 s of *all* ticks. Keep a separate estimate for ticks that start notes (the step
   tick at 11-29 samples) and for the cheap pending ticks (1 sample), or reserve by the expected work. A tick-only
   reservation still reserves 11-29 samples in the pending-tick renders.
5. Then revisit direness. With a sane margin it should leave 14. Measure the voice-start cap (1 vs 4) and the chord
   latency (cullsim), and the render cost increase from the band-limited paths (ins/frame in full mode).
   Benchmarks: **BU6 (3 runs), KIT20, BKIT20U4**. BU8 is the "real overload must still cull" control.

**E2 (cheaper voice start).**
- Per start: 5.5k instructions. 58% is Patcher::performInitialPatching (3.2k), the rest is Voice::noteOn setup.
- The bigger, easier tick win: a *refused* start still costs about 1.1k instructions (Arpeggiator::noteOn bookkeeping
  with the arp off, plus reassessRenderSkippingStatus), x19 per step = 3.3 samples of the 10.8-sample step tick.
- In kits, the per-note kit overhead is about 3.7k (on) / 3.1k (off) instructions: getNoteRowForDrum linear search
  plus the kit arpeggiator with mode OFF. Skipping the kit arp when it's off and passing the row/drum index down
  would cut the kit step tick from about 27 to about 8 samples (KIT20/BKIT20U4).
- Starting 4 voices per render instead of 1 would cost about 2.6 extra samples per render, and remove about 15 ms
  of chord smear.

**E3 (spread the tick).**
- In synth songs the step tick is only 11 samples, split roughly evenly: note-on 32%, note-off 21%, arp tick-forward
  21%, row scan 17%. Spreading it buys at most about 11 samples once a step, and E1 already removes most culls.
- In kits the step tick is 27-29 samples, 41-58% of the step-tick render, and the next render's margin falls to 7-9
  (KIT20, BKIT20U4 dStep 1). That's where spreading pays (lookahead the note rows, or process the note-offs a render
  early). But the E2 kit fix above is simpler and removes 2/3 of it.
- Also worth considering: the pending-note ticks (+1..+3 song ticks, about 1 sample each) cut 3-4 render windows short
  per step for nothing. They exist only so handlePendingNotes() runs from doTickForward. Pending notes already start
  from Sound::render, so these ticks could probably go: fewer truncated renders, less fixed cost (5-10 samples per
  render), and fewer prediction mismatches.

**E4:** The tick path does no measurable GMA allocation in these songs (no FX). For E4, use the coordinator's FX
songs (FXALL, KITCHAIN), not these.

## Run inventory (`/home/user/perfwork/runs/`)

- base-{N30,U4}-lean1 (run_emu.py, ring only)
- base-{BU5,BU6,BU7,BU8,BU4V24,BU4V28,KIT20,BKIT20U4,FX,N10}-lean1, base-{BU6,BU8,KIT20,BKIT20U4}-lean2
  (run_emu_rings.py, renders_all.bin)
- base-BU6-hot + base-BU6-toplay (hotblocks; bucket.txt / bucket_all.txt in base-BU6-hot)
- base-BU6-full, base-BU6-full-scopesB, base-BKIT20U4-full (standard tick scopes), base-BKIT20U4-full-scopesB
  (scopes.txt lists the functions)
- base-BU6-flame: an empty profile (dzstack without capstone, see section 3)
- base-ladder.txt; cullattr.txt / cullsim.txt in the BU6, BU8, KIT20 and BKIT20U4 run dirs

No firmware changes: no new probe fields were needed, and there is no probe-base-v2.elf.
