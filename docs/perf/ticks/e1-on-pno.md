# E1 on the pending-note-on time budget: tick-aware, coherent cull and direness margins

E1 resumed on top of PR #18: the voice-start time budget, squash-merged to the fork's main as 3755636a2. The previous
E1 notes (`e1-tick-aware-cull.md`) cover main 680b93c.

Review PR: https://github.com/m-m-adams/DelugeFirmware/pull/19 (into perf/performance-review).

## TL;DR

- Branches:
  - **Review branch:** `perf/ticks/claude-e1-tick-aware-cull-review` (local `perf/e1-review`). 7 commits on origin/main
    3755636a2, firmware only.
  - **Experiment branch:** `perf/ticks/claude-e1-on-main` (local `perf/e1-on-main`). The same tree, with the
    history as measured (kRenderSafetyMargin 16 -> 20 is a separate commit there), plus this doc.
  - **First version:** `perf/ticks/claude-e1-on-pno`, on 09d4d3a.
  - **Probe builds** (not for merging): `-e1-on-main-probe2` (final measurement build), `-e1-on-main-probe`,
    `-e1-on-pno-probe{,2,3}`.
- Bar (vs 09d4d3a, which is the same as main for these files, with fc0bc1e's measured-start leniency): **met.**
  - **No render ran past empty or finished within 4 samples of empty, in any of the 10 final runs.**
  - Culls/5 s compared with 09d4d3a:
    - KIT20: 0 (was 0).
    - BKIT20U4: 14.9, 3-run mean (was 19.6).
    - BU6: 7.0 (was 10.2).
    - BU8: 165, still culling (was 148).
  - Hard cull events: **0 in every song.** They were 0.4 in BU6 and 5.2 in BU8.
  - BU8's least time left goes from 6.2 to 17.0 samples.
- **Direness is no longer pinned at 14.** Mean direness is now:
  - 3.0 in KIT20 (69% load);
  - 7.1-7.2 in BKIT20U4 and BU6 (87-88% load);
  - 0 in N10;
  - still 14 in the genuinely render-bound BU8.

  So band-limited oscillators, cubic sample interpolation and ladder-filter oversampling come back whenever there's
  real headroom between steps. That costs some culls in BKIT20U4: 5.5 without the direness change, 14.9 with it.
- **Chord smear is unchanged:** the time budget (09d4d3a) already halved it, and E1 doesn't change it further.

## The stack (review branch, in order)

| # | commit | what | why |
|---|---|---|---|
| 1 | `35b13297c` (fc0bc1e, reworded) | cullVoices()' leniency = what the last render's voice starts took (~1-4 samples) instead of 20 if any started | realistic. Alone it raised culls x2-4 (tb5), because of the prediction errors below |
| 2 | `4a99daace` (E1 a) | reserve songTickCost only when a tick is due at the window start; `getTimeTilNextSongTick()` factored out of tickSongFinalizeWindows(); used by setDireness, getRenderSize and the between-render voice budget | ticks only ever start a window |
| 3 | `30a490fd0` (E1 b) | predict a window that a tick cuts short at its real size | |
| 4 | `de85a82cc` | getRenderSize(): keep the time available signed (size_t wrap) | latent on main; matters with a bigger margin |
| 5 | `c2cf91d42` | kRenderSafetyMargin 8 -> 20 | (2) removes the padding the doubled renders had. 20 also keeps a catch-up render above the hard-cull line (16), even after rounding up to a multiple of 4 |
| 6 | `14b48cb53` | away from song ticks (more than kTickRecoveryRenders = 3 renders after a tick render), soft cull whenever the margin is over the limit, not only when it's rising | the steady-state overload signal BU8 needs (see below) |
| 7 | `494a56ddc` | direness from the margin of the smallest render that keeps up (the space, or up to the next tick), and not raised in the 3 renders after a tick | direness reflects real headroom; thresholds unchanged |

One margin definition, `queued - songTickReserve(timeTilNextTick) - renderCost.predict(size)`, is used everywhere:
- setDireness() culls on it for the render getRenderSize() picks (truncated at a tick);
- getRenderSize() caps doubling on it;
- the voice budget uses it between renders, with the same tick query, and in renders as
  `queued - time spent - predict(size)`, where the tick is already spent rather than reserved;
- direness uses it for the keep-up size.

What's derived from it:
- getRenderSize() keeps >= kRenderSafetyMargin (20).
- The budget starts voices while it stays above kCullMargin (48) after the charge.
- Soft culls happen at <= 48: away from ticks always, right after a tick only if rising.
- Hard culls happen at <= 16, which (5) keeps catch-up renders from reaching.
- Direness rises at <= 78 and reaches 14 at <= 65, on the keep-up size.

## Results (lean, Deluge speed, sd-baseline.img, 200 slices, 25 s skipped)

Columns:
- culls/5 s per run, with (hard/soft events per 5 s, mean);
- voices; load;
- least queued at a render start;
- least time left when a render finished (queued at start - total time);
- renders past empty / within 4 samples of empty, summed over the runs;
- mean direness (and % of renders at 14);
- note delay mean / p90 (ms); chord smear mean / max (ms).

Baseline = 09d4d3a + probe (`tbf-*` runs, from E3). It has the same audio_engine.cpp, deferred_note_ons and voice code
as main 3755636a2. Main only adds unrelated USB-audio and note-editing commits. Final = main + this stack + probe
(`e1p-m2-*` runs).

| run | culls/5 s (hard/soft) | voices | load | least queued | least left | past empty / <4 | direness (14) | note delay | chord smear |
|---|---|---|---|---|---|---|---|---|---|
| KIT20 09d4d3a | 0.0 (0.0/0.0) | 20.0 | 69% | 89 | 46.4 | 0 / 0 | 14.0 (100%) | 2.6 / 4.7 | 4.7 / 5.3 |
| **KIT20 final** | **0.0** (0.0/0.0) | 20.0 | 69% | 89 | 46.0 | **0 / 0** | **3.0 (0%)** | 2.6 / 4.7 | 4.8 / 5.4 |
| BKIT20U4 09d4d3a (3) | 21.8 / 18.7 / 18.4 (0.0/19.6) | 19.3 | 87% | 81 | 14.3 | 0 / 0 | 14.0 (100%) | 4.8 / 6.7 | 7.0 / 8.6 |
| **BKIT20U4 final (3)** | **13.8 / 16.2 / 14.8** (0.0/14.9) | 19.2 | 87% | 81 | 9.2 | **0 / 0** | **7.1 (0%)** | 4.8 / 6.7 | 7.0 / 8.6 |
| BU6 09d4d3a (3) | 10.4 / 10.7 / 9.8 (0.4/9.5) | 19.4 | 88% | 81 | 15.9 | 0 / 0 | 14.0 (100%) | 5.0 / 6.9 | 7.2 / 8.4 |
| **BU6 final (3)** | **7.0 / 7.1 / 6.8** (0.0/7.0) | 19.3 | 88% | 80 | 15.5 | **0 / 0** | **7.2 (0%)** | 5.1 / 7.1 | 7.3 / 8.4 |
| BU8 09d4d3a (2) | 148.6 / 147.2 (5.2/137.5) | 16.5 | 89% | 73 | 6.2 | 0 / 0 | 14.0 (100%) | 5.3 / 7.4 | 7.9 / 9.6 |
| **BU8 final (2)** | **165.4 / 165.4** (0.0/165.4) | 15.7 | 88% | 79 | **17.0** | **0 / 0** | 14.0 (100%) | 5.0 / 7.1 | 7.7 / 9.2 |
| N10 09d4d3a | 0.0 | 10.1 | 44% | 109 | 90.8 | 0 / 0 | 0.0 | 0.4 / 1.1 | 1.1 / 1.3 |
| **N10 final** | 0.0 | 10.1 | 44% | 109 | 90.8 | 0 / 0 | 0.0 | 0.4 / 1.2 | 1.1 / 1.3 |

The stack, step by step (1 run each unless noted; the BU8 row is the blocker):

| build | BKIT20U4 | BU6 | BU8 | KIT20 |
|---|---|---|---|---|
| fc0bc1e only (tb5) | 40.8 (16.7 hard) | 33.8 (4.0 hard) | 155.8, least left 5.8 | 0 |
| + E1 a, b, fix, margin 16 (`p1`) | 5.8 (1.4 hard) | 4.8 | 98.8, **9 past empty, 14 <4**, least left -0.9 | 0 |
| + steady soft cull (`p2`) | 5.5 (0 hard) | 5.7 | 162.5 (0 hard), least left 17.1, 0 / 0 | – |
| + direness (`m`, margin 16, 3/3/2 runs) | 17.6 / 16.9 / 17.7 (0.7-1.5 hard) | 7.2 / 7.9 / 7.4 | 163.4 / 163.2, 0 / 0 | 0 |
| + margin 20 = final (`m2`) | 13.8 / 16.2 / 14.8 (0 hard) | 7.0 / 7.1 / 6.8 | 165.4 / 165.4, 0 / 0 | 0 |

The full table is in `runs/e1p-table.md` (from `tools/e1table.py`).

## What each step fixed

- **fc0bc1e + E1 a/b** takes BKIT20U4 from 40.8 to 5.8 and BU6 from 33.8 to 4.8. The extra culls fc0bc1e exposed were
  the songTickCost reserve in non-tick renders and the misprediction of tick-truncated windows. This replays exactly
  (`tools/e1sim.py --leniency measured`, events within 0.3/5 s of recorded).
- **BU8 underran with E1 a/b alone** (9 renders past empty, all in dStep 2, rendering the old chord's release plus the
  new voice, with space grown to 62-66):
  - The full chord (20 x unison 8) costs about 1 sample of time per sample. Between steps, the margin sat at 36-52,
    under the cull limit half the time. But the soft cull only fires while the margin is *rising*, so the song
    settled at 17.6 voices.
  - In 09d4d3a, the songTickCost reserved in every render pulled those margins 8 samples lower. That made the soft
    cull fire about 3x as often there (0.185 vs 0.058 culls per render), which kept 16.5 voices and enough headroom
    for the step transient.
- **Steady-state soft cull (step 6)** makes that explicit: more than 3 renders after a tick render, a margin under the
  limit means the voices playing don't fit, so cull without waiting for it to rise. Right after a tick the dip is
  the tick catching up, so the "rising" rule stays.
  - BU8: 15.8 voices, 0 hard, least left 17.1 (09d4d3a 6.2).
  - BU6, BKIT20U4 and KIT20: unchanged, because their between-step margins are above the limit (p1 60-66).
- **Margin 16 -> 20 (step 5, folded):** with direness down, BKIT20U4's render after the kit tick catches up by
  doubling to the getRenderSize() cap. At 16, that is exactly the hard-cull line, and rounding the render up to a
  multiple of 4 crossed it: 0.7-1.5 hard events/5 s, all at dStep 1. At 20 that's 0, and soft culls went down too.

## Direness

What it gates:
- saw / square / supersaw use the crude, non-band-limited render for wavetables below `direness + 6`;
- analog saw becomes the digital saw for tables 8..direness+5;
- triangle takes the cheap path at >= 7;
- samples use linear interpolation from octave `26 - direness/4` up;
- ladder-filter oversampling (lpladder) and hot-ladder drive oversampling are on only below 14.

The voice-start cap no longer depends on direness (the 09d4d3a time budget replaced it).

Why it was pinned:
- It was set from the cull margin of the doubled render. getRenderSize() pushes that margin toward its cap at any
  load above about half.
- That margin also dips right after every tick, and direness only falls 1 step per 1/8 s, which is the step rate at
  120 BPM 16ths.

Re-derivation: same thresholds, different input.
- The keep-up margin (the space, or up to the next tick) measures the CPU's real headroom.
- The tick-recovery renders are skipped.
- Static replay on the p1 rings (`tools/e1dire2.py`):

  | song | thresholds as they were (shift 0) | thresholds 10 lower |
  |---|---|---|
  | BU6 | 7.2 (5-8) | 0 |
  | BKIT20U4 | 6.4 | 0 |
  | KIT20 | 3.6 | 0 |
  | BU8 | 14 | 13.3 |

  Lowering the thresholds would mean full quality, but at the cost of more culls, so they stay where they were.
- Measured: KIT20 mean 3.0, BKIT20U4 7.1 (5-11), BU6 7.2 (6-8), N10 0, BU8 14.
- What that buys: at 7, the crude saw only covers tables below 13 instead of 20; ladder oversampling is back;
  interpolation is cubic up to octave 24 instead of 22.
- Cost: render time per voice goes up, so BKIT20U4 culls went 5.5 -> 14.9 (all soft) and least time left 15.3 -> 9.2.
  BU6 went 5.7 -> 7.0. Both are still under 09d4d3a.
- This is commit 7, separate. A reviewer who prefers fewer culls to better quality can drop it; the rest stands alone.

## Risks / what to look at

- The steady-state soft cull is a behaviour change: one force-release per render while the margin is under the limit
  away from ticks.
  - It doesn't fire in KIT20, BKIT20U4 or BU6.
  - A song whose ticks come more often than every 4 renders (dense arps, very high tempo or resolution) never counts
    as steady, so it keeps the old rule. That's no regression, but no benefit either.
  - With no clock running (auditioning, MIDI live play), every render counts as steady, so an overloaded live
    performance now soft culls without waiting for a rising margin. That's intended, but untested on hardware.
- kTickRecoveryRenders = 3 is empirical: the dips last 1-3 renders after a tick here.
- The direness change raises render cost wherever headroom exists. Culls in heavy kits rise relative to the step
  without it (still under 09d4d3a). Quality changes are audible by design: band-limited oscillators, i.e. less
  aliasing.
- The probe records the cull margin, not the keep-up margin, so the direness input isn't in the ring directly.
- External clock: no reserve until a swung tick is scheduled; the next render that starts with it reserves it.
  Untested.

## Not tested

- Hardware: nothing.
- Stem export: the offline path is unchanged by E1; 09d4d3a's own note covers it.
- External clock; songs with audio clips / time-stretch; FX-heavy songs (FXALL, KITCHAIN). E4's deferred-allocation
  songs weren't run either.
- Full-mode instructions-per-frame cost of the direness change.

## Composition with E3/E2

- E3 (#18, now main) is the base: one tick per step, smaller songTickCost, the time budget.
- E2's cheaper note-on path makes the ticks smaller, which shrinks the tick reserve further. It doesn't touch the
  margin definitions. Not measured together.

## Commands

```sh
T=/home/user/perfwork/tools
# runs: rb2.sh STAGE SONG:N ...  (elf/e1p/probe-STAGE.elf -> set_song -> run_emu_rings_pno.py --lean)
$T/tb_report.py runs/e1p-m2-*            # load, culls, least queued/left, past empty/<4, note delay, smear
$T/e1table.py "LABEL=RUN,RUN" ...        # the table rows above
$T/e1sim.py RUN --leniency measured --safety 16   # replay setDireness/getRenderSize/cullVoices variants (validated)
$T/e1soft.py RUN                         # soft-cull variants on recorded margins (as is / consecutive / steady)
$T/e1dire2.py RUN --shift 0 10 20        # direness replay: as is / skip tick dips / keep-up margin
$T/e1steady.py RUN                       # steady-state margin from the RenderCost fit (explored, not used)
```
