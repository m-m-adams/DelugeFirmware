# E1: tick-aware cull prediction and coherent margins

**Status: stopped early (coordinator, 2026-11-20 ~03:30). The results below are incomplete; see "Not done".**

Worktree `/home/user/wt/e1-tick-aware-cull`, off main 680b93c. All numbers are from the emulator at Deluge speed
(`timectl=1,cachesim=0,cpi=1.45`, lean, 200 slices, 25 s skip), card `/home/user/perfwork/sd-baseline.img`, the same
way as baseline.md describes.

## TL;DR

- The **recommended stack** is branch `perf/e1-tick-aware-cull`, 4 commits:
  - (a) reserve songTickCost only when a tick is due at the window start;
  - (b) predict the render that a tick cuts short at its real size;
  - a signed-arithmetic fix in getRenderSize();
  - kRenderSafetyMargin 8 -> 16.
- Against the baseline, it gives:
  - **BU6**: 2.7 culls/5 s (one run so far) against 8.5-15.9.
  - **BKIT20U4**: 13.0 against 53-58.
  - **BU8**: 109 against 191. It still culls, as a render-bound song should.
  - Voices are up, load is unchanged, and min queued is unchanged except in BU8 (58 against 73).
- **Safety caveat:** in BU8, 1 render in 53 s finished with the codec buffer overrun by 8.9 samples. The baseline had
  0 in 2 runs. That's not good enough to upstream yet.
- **(c), "judge overload on the minimum render size", is rejected.** It gives 0 culls in BU6, KIT20 and BKIT20U4, but
  in BU8 the codec underruns 15-23 renders per 5 s. The baseline's "phantom" over-prediction (the doubled size plus a
  tick reserve in every render) was in practice the only early warning for a song whose full chord costs more than
  real time. The variants I tried for (c) all fail BU8: min size, doubling capped at the cull margin, and min size
  plus a 16-sample render margin plus a hard cull without the voice-start allowance.
- **(a) removes padding the doubled renders relied on.** With (a), the songTickCost that getRenderSize() used to keep
  in every render is gone, and an 8-sample render margin is less than how far the render after a step's tick is
  under-predicted (4-9 samples, up to 15). In a and a+b, BKIT20U4 and BU8 renders ran within 4 samples of empty
  6-13 times per 5 s. kRenderSafetyMargin = 16 fixes that (0 in BKIT20U4).
- **Direness stays at 14 in 100% of renders in every variant**, and the chord start latency is unchanged at 13-15 ms.
  It can't come down while the margin is judged on the doubled render. With the minimum-size margin, its thresholds
  would have to move about 30 samples lower to leave 14 (BU6 replay: direness 0-9).
- **(d)**, a separate cheap-tick estimate, wasn't needed: no predicted cull in a pending-tick render in any replay.

## Changes

### Recommended: `perf/e1-tick-aware-cull` (off 680b93c)

| commit | what |
|---|---|
| `2c4a3e9` | (a) Only reserve songTickCost for a render that starts with a song tick. Factors `getTimeTilNextSongTick()` out of tickSongFinalizeWindows() (a pure read of the timer/swung tick schedule), so routine_() calls it once before setDireness() and passes it to setDireness() and getRenderSize(). `songTickReserve(t) = t <= 0 ? songTickCost.get() : 0`. |
| `c38bf9c` | (b) In setDireness(), `renderSize = min(getRenderSize(...), timeTilNextTick)` when a tick falls inside the window. |
| `b881680` | getRenderSize(): `SSI_TX_BUFFER_NUM_SAMPLES - spaceInBuffer - kRenderSafetyMargin` was unsigned (spaceInBuffer is size_t). It wraps to about 4e9 once less than the margin is queued, and then the render doubles to 128 instead of being capped. That's latent on main (only with more than 120 samples of space), and it **caused 55-sample overruns** in my first margin-56 build. |
| `c221ba5` | kRenderSafetyMargin 8 -> 16 (see Safety). |
| (docs commit) | this file under docs/perf/ticks/ (not for merging) |

Key hunks (full diff `git diff 680b93c c221ba5`, +60/-24 lines in audio_engine.cpp):

```diff
+float songTickReserve(int32_t timeTilNextTick) {
+	return timeTilNextTick <= 0 ? songTickCost.get() : 0.f;
+}
-constexpr int32_t kRenderSafetyMargin = 8;
+constexpr int32_t kRenderSafetyMargin = 16;
 ...
-		float timeAvailable =
-		    (float)(SSI_TX_BUFFER_NUM_SAMPLES - spaceInBuffer - kRenderSafetyMargin) - songTickCost.get();
+		float timeAvailable = (float)(SSI_TX_BUFFER_NUM_SAMPLES - (int32_t)spaceInBuffer - kRenderSafetyMargin)
+		                      - songTickReserve(timeTilNextTick);
 ...
-	size_t renderSize = getRenderSize(numSamples);
-	int32_t predicted = (int32_t)(songTickCost.get() + renderCost.predict(renderSize));
+	size_t renderSize = getRenderSize(numSamples, timeTilNextTick);
+	// If a tick falls within the window, tickSongFinalizeWindows() will cut the render short there
+	if (timeTilNextTick > 0) {
+		renderSize = std::min<size_t>(renderSize, timeTilNextTick);
+	}
+	int32_t predicted = (int32_t)(songTickReserve(timeTilNextTick) + renderCost.predict(renderSize));
 ...
-	setDireness(numSamples);
+	// Whether the song ticks at the start of this window - and if not, where the window will be cut short for one
+	int32_t const timeTilNextTick = getTimeTilNextSongTick();
+	setDireness(numSamples, timeTilNextTick);
 ...
-	numSamples = getRenderSize(numSamples);
+	numSamples = getRenderSize(numSamples, timeTilNextTick);
```

Why (a) is exact: a tick is only ever actioned at the start of a window, because tickSongFinalizeWindows() cuts a window
short at the next tick. The tick schedule can't change between setDireness() and tickSongFinalizeWindows(): inputTick()
and the clock routines run as tasks, not ISRs. With an external clock and no swung tick scheduled yet, the query gives
9999, so nothing is reserved. Once the input tick schedules one, even in the past, the next routine_() sees it due and
reserves it.

### Rejected variants (kept as branches)

| branch | commits on top of | what | why rejected |
|---|---|---|---|
| `perf/e1-cmin-rejected` | a+b: `84a1634` | (c) judge on the minimum render: `renderSize = min(space, timeTilNextTick)` in setDireness() | BU8 underruns 23/5 s; BKIT20U4 doubled renders within 4 samples of empty 21/5 s |
| `perf/e1-s56-rejected` | a+b+fix: `fcc7d37` | (c') kRenderSafetyMargin = kCullMargin + 8, so doubling never takes the margin into cull range | smaller renders mean less catch-up, so *less* queued: BU6 minQ 60 (against 84) and 9.7 culls; BU8 302 culls/5 s and 12 underruns/5 s |
| `perf/e1-cmin-hard` | a+b+fix: `fbb3bfd` (min size), `ce53bc3` (margin 16), `5f3efed` (hard cull on the margin alone, without the +20 per started voice) | (c'') | BU6/BKIT20U4/KIT20 0 culls and safe, but BU8 15 underruns/5 s |

Measurement branches: the probe e339b27 cherry-picked cleanly onto each. They're not for merging:
- `perf/e1-probe-a` (1be11d6)
- `perf/e1-probe-ab` (6a55c78)
- `perf/e1-probe-abc` (b9a961a, = cmin)
- `perf/e1-probe-abs` (358ac9a, = s56 with the fix)
- `perf/e1-probe-f1` (3c63485, = cmin-hard)
- `perf/e1-probe-abf` (bc6a8de, = the recommended stack)

The probe still records `tickCost` as songTickCost.get(), not the reserve. Whether it was reserved is `ticks > 0`.
ELFs are in `/home/user/perfwork/elf/e1/probe-e1-*.elf`, and song copies in `elf/e1-<stage>-<song>.elf`
(the `absbug` ELF is the margin-56 build without the signed fix).

## Prediction before building (tools/e1sim.py)

`e1sim.py` replays setDireness()/getRenderSize() on the baseline rings. It rebuilds the RenderCost fit and getRenderSize()
from the ring. It reproduces the recorded margin to within 0.05 samples on average, and the recorded cull events exactly.
Cull events/5 s (static: the voices that fewer culls would leave playing aren't modelled):

| rule | BU6 (2 runs) | BU8 | KIT20 | BKIT20U4 (2 runs) | BU7 | BU4V28 |
|---|---|---|---|---|---|---|
| as is | 8.1 / 15.1 | 116 | 1.2 | 50.5 / 49.0 | 77 | 93 |
| a | 0.7 / 1.8 | 78 | 0.2 | 13.1 / 10.2 | 15.4 | 31.9 |
| a+b | 0.7 / 1.8 | 78 | 0.2 | 13.1 / 10.2 | 15.2 | 31.8 |
| a+b+c (min size) | 0 / 0 | 0.1 | 0 | 1.4 / 1.7 | 0 | 0 |
| a+b, margin 56 | 0 / 0 | 1.3 | 0 | 0 / 0 | 0 | 0 |

The static replay was right for the songs that aren't overloaded (measured a: BU6 2.2, BKIT20U4 19). It was badly
wrong for BU8, where fewer culls let more voices play, and those make the renders heavier (see Safety). (b) changes
almost nothing on its own. The pre-tick render's over-limit reading mostly came from the tick reserve, which (a)
already removes.

## Results (measured)

`culls/5s` = voices culled; `ev h/s` = cull events hard/soft per 5 s; `left` = least (queued at start - render's total
time) at the end of a render, so negative = the codec ran past the write point. `under` = renders with left < 0 per 5 s
(< 4 in brackets). Direness was **14 in 100% of renders in every run** (KIT20 cmin: 97%). Chord latency is
cullsim's metric.

| run | culls/5s | ev h/s | voices | load | size mean (p10/50/90) | minQ (<16) | left | under (<4) | chord lat |
|---|---|---|---|---|---|---|---|---|---|
| **BU6** base x3 | 8.5 / 15.9 / 12.9 | 0.4-0.8 / 7.7-14.3 | 18.7 | 87% | 40.1 (28/36/52) | 84 (0) | 13.2 | 0 (0) | 13.1 ms |
| BU6 a | 2.2 | 0.3/1.5 | 18.8 | 88% | 40.5 (28/36/52) | 84 (0) | 13.2 | 0 (0) | 13.2 ms |
| BU6 a+b | 2.0 | 0.7/0.6 | 18.8 | 88% | 40.5 | 85 (0) | 13.3 | 0 (0) | 13.2 ms |
| **BU6 a+b+fix+m16 (rec.)** | **2.7** (1 run) | 0.9/0.9 | 18.8 | 88% | 40.5 (28/36/52) | 85 (0) | 13.3 | 0 (0) | 13.2 ms |
| BU6 cmin | 0.0 | 0/0 | 18.8 | 88% | 40.5 | 85 (0) | 17.1 | 0 (0) | 13.1 ms |
| BU6 cmin-hard | 0.0 | 0/0 | 18.8 | 88% | 40.5 | 84 (0) | 14.0 | 0 (0) | 13.1 ms |
| BU6 s56 | 9.7 | 1.3/7.1 | 18.8 | 88% | 40.0 (28/40/48) | 60 (0) | 0.7 | 0 (0.4) | 13.0 ms |
| **BKIT20U4** base x2 | 57.9 / 52.6 | 4-7 / 44 | 18.0 | 86% | 33.9 (24/32/44) | 72 (0) | 8.8 | 0 (0) | 13.2 ms |
| BKIT20U4 a | 19.2 | 2.1/14.9 | 18.5 | 86% | 35.6 (24/32/48) | 73 (0) | 0.8 | 0 (6.0) | 13.2 ms |
| BKIT20U4 a+b | 19.0 | 1.6/15.7 | 18.5 | 86% | 35.5 | 72 (0) | 0.8 | 0 (6.2) | 13.2 ms |
| **BKIT20U4 rec.** | **13.0** (1 run) | 1.7/9.5 | 18.6 | 86% | 35.9 (24/32/48) | 73 (0) | 7.7 | 0 (0) | 13.3 ms |
| BKIT20U4 cmin | 0.0 | 0/0 | 18.9 | 87% | 36.7 | 71 (0) | -0.2 | 1.65 (20.6) | 13.6 ms |
| BKIT20U4 cmin-hard | 0.0 | 0/0 | 18.9 | 87% | 36.6 | 73 (0) | 7.9 | 0 (0) | 13.5 ms |
| BKIT20U4 s56 | 8.8 | 0/8.8 | 18.8 | 87% | 36.0 | 57 (0) | 2.9 | 0 (0.7) | 13.3 ms |
| **BU8** base x2 | 191.7 / 191.1 | 75/42 | 14.9 | 87% | 38.2 (24/40/52) | 73 (0) | 4.1 | 0 (0) | 14.1 ms |
| BU8 a | 133.3 | 22/89 | 16.6 | 89% | 47.9 (28/48/60) | 69 (0) | -1.1 | 1.65 (13.2) | 14.9 ms |
| BU8 a+b | 118.0 | 22/73 | 16.6 | 89% | 47.5 | 69 (0) | -1.1 | 1.38 (13.0) | 14.8 ms |
| **BU8 rec.** | **108.7** (1 run) | 8.9/90.8 | 16.6 | 89% | 47.7 (28/48/60) | 58 (0) | **-8.9** | **0.09** (0.09) | 14.9 ms |
| BU8 cmin | 3.3 | 0.4/2.6 | 18.1 | 91% | 58.8 (28/60/72) | 53 (0) | -21.5 | 23.2 (39) | 14.8 ms |
| BU8 cmin-hard | 62.3 | 31/16 | 17.2 | 90% | 52.0 | 52 (0) | -14.8 | 15.5 (20.7) | 14.7 ms |
| BU8 s56 | 302.5 | 128/33 | 12.8 | 84% | 30.5 (16/32/44) | 48 (0) | -19.1 | 12.0 (33.8) | 13.3 ms |
| **KIT20** base x2 | 1.2 / 1.2 | 0/1.2 | 19.3 | 69% | 27.4 (24/28/32) | 78 (0) | 43.4 | 0 | 10.5 ms |
| KIT20 cmin | 0.0 | 0/0 | 19.3 | 69% | 27.4 | 79 (0) | 32.8 | 0 | 10.6 ms |

No render in any run *started* with less than 16 queued (minQ >= 48). The underruns are all renders that took longer
than what was queued. The baseline's own minimum was 4.1-4.7 (BU8) and 8.8 (BKIT20U4). Tables:
`runs/e1-report.txt`, `runs/e1-underruns.txt`. Run dirs: `runs/e1-<stage>-<song>-lean1`.

## Safety: what went wrong, and why

Tools: `e1prederr.py` (the render-time prediction error, by renders since the step tick) and `e1neg.py` (the renders that
ran short).

1. **The render right after the step tick (dStep 1) is under-predicted** by 4-9 samples: p50 4-7, p99 8-9. It renders
   the old chord's voices, which the tick has just released, plus a doubled size, because the tick drained the queue.
   In BU8, steady doubled renders are off by up to 12-15. With the baseline's songTickCost in every getRenderSize()
   cap, the real headroom was 8 + 11..29 samples. (a) takes that padding off, so a and a+b ran BKIT20U4/BU8 dStep-1
   renders to within 4 samples of empty 6-13 times per 5 s, and BU8 past empty 1.4-1.7 times per 5 s.
   **kRenderSafetyMargin = 16 removes all of those** (rec.: 0 in BKIT20U4, BU8's d1 cases gone).
2. **BU8 is genuinely overloaded in a way the minimum-size margin can't see in time.** With the full chord (20 voices
   x 8 unison), a render costs 1.0-1.2 samples of time per sample. So even renders of just the space fall behind:
   space goes 40 -> 54 -> 66 -> 72 in 2-3 renders. And the RenderCost fit (15/16 decay) lags by up to 11 samples
   while the chord trickles in one voice per render. Judged on the minimum render, the margin goes from about 70 to
   below 0 within 3 renders. The soft cull is one voice, and only while over-limit and rising, which a truncated
   pre-tick render in between can reset. The hard cull needs margin <= -4 after a voice start. Together they react
   too late. The baseline's doubled-size reading was about 10-30 samples more pessimistic, so it culled BU8 down to
   15 voices before the full chord ever played. The phantom margin was doing real work there.
3. **Capping doubling makes things worse.** A render bigger than the space *refills* the buffer when the cost per
   sample is below 1. Capping doubling at the cull margin left BU6 sitting at 60 queued instead of 84.
4. **The remaining risk in the recommended stack:** BU8 (one run) had a single dStep-2 render 8.9 samples short:
   - The margin was 17 at dStep 1, then 2 at dStep 2. Both renders came right after a voice start, so the
     allowance applied: no hard cull, and dStep 1 wasn't "rising".
   - Dropping the voice-start allowance for hard culls would have caught it. But with the margin judged on the doubled
     render, that also brings back phantom hard culls. A replay of the recommended BKIT20U4 run gives 1.7 -> 11.1
     hard events/5 s. So I didn't put it in the stack.

## Direness and chord latency (step e)

- Measured: **direness 14 in 100% of renders in every run.** Chord start latency is 13.1-13.6 ms for BU6/BKIT20U4
  (19 renders, 1 voice per render) and 14.7-14.9 ms for BU8. That's the baseline's value, because what direness gates
  is unchanged. BU8 is slightly longer because more voices stay up.
- With the doubled-render margin (the recommended stack), even moving the direness thresholds 40 samples lower leaves
  it at 14 (`e1dire.py` on the rec. BU8/BKIT20U4 runs). The doubled render's margin sits near the getRenderSize()
  cap in every busy render.
- With the minimum-size margin (cmin-hard, BU6), direness comes down only with the thresholds about 30 lower:
  - shift 20: 14 in 44% of renders;
  - shift 30: direness 0-9, never above 12, so 4 voice starts per render;
  - shift 40: 0.
- Direness's decay (1 step per 1/8 s) is the same as the step rate at 120 BPM 16ths, so one dip per step pins it.
  Bringing it down needs both a margin that means something (min size) and lower thresholds or a different decay.
  Neither was in scope.

## Composition with E3 (a729ab9: hold back refused note-ons)

- E3 shrinks the real tick: songTickCost goes 11 -> 8 (synth) and 27-29 -> 16-17 (kit).
- E3 also removes the pending-note ticks: 32 -> 8 ticks/s. That leaves one tick render per step and no truncated
  pre-tick renders for (b) to fix.
- E1 (a) makes the reservation follow the tick. Together, the reserve is both smaller and rarer, and the two act on
  different terms of the prediction (E3 on the cost, E1 on when it's charged), so they compose.
- The BU8 transient that defeats (c) is the old chord's release plus the full chord. Neither change addresses it.
- E3's held-back notes start in the same renders as pending notes did, so E1's prediction is unaffected.
- E1 + E3 wasn't measured.

## Risks

- Less over-prediction means more voices survive, and the codec then has less slack in real overload. Watch songs
  whose full voice load is above 100% CPU (BU8-like). One underrun in about 53 s of BU8 (1 run) in the recommended
  stack, against 0 in the baseline's 2 runs.
- kRenderSafetyMargin 16 lowers the doubling cap. No measurable load or size change in BU6/BKIT20U4.
- External clock: with no swung tick scheduled, nothing is reserved until one is. That's correct (the tick will start
  a later window, which then reserves it), but untested on hardware/emulator with an external clock.

## Not done (stopped)

- Recommended stack: only 1 run each of BU6 (needs >= 3: noise ±40%), BKIT20U4 and BU8 (a second was queued). KIT20
  wasn't run (it was interrupted). a and a+b BU6: 1 run each, and KIT20 not run.
- No full-mode (instructions per frame) runs.
- An overload detector that would make (c) safe (and let direness come down) wasn't tried. Ideas:
  - judge the minimum render *and* the next one ("can the CPU keep up for two renders") with a faster-reacting cost
    estimate during voice ramps (e.g. the max of the fit and the last render's cost per sample);
  - a hard cull independent of voice starts, once the margin is judged on the minimum render.
- (d) wasn't implemented: no replay showed culls in cheap-tick renders.
- The plain (no-probe) build of the final branch wasn't built separately. Every commit compiled as part of the probe
  builds, and the probe only adds hooks.
- An upstream PR wasn't opened (not asked). Draft below.

## Draft upstream PR description (for a, b, fix, margin 16)

> **Only reserve the song tick's cost for the render that starts with a tick**
>
> setDireness() and getRenderSize() took songTickCost (the most a song tick has cost in the last 2 s, 11-29 samples in
> busy songs) off every render's predicted margin, whether or not a tick was due. Ticks are only ever actioned at the
> start of a render window: a window that a tick falls in is cut short there. So reserve the cost only when one is
> due at the start of the window. The time to the next tick comes from a new side-effect-free
> getTimeTilNextSongTick(), factored out of tickSongFinalizeWindows(). Also predict a window that a tick will cut short
> at its real size.
>
> The reservation had also been padding getRenderSize()'s headroom for catch-up renders, so kRenderSafetyMargin goes
> from 8 to 16 samples. The render right after a step's tick is regularly 4-9 samples slower than predicted.
> Also fixes an unsigned underflow in getRenderSize()'s time available.
>
> Emulator, Deluge timing, 20 voices started per 16th step:
> - synth chord song: 8.5-15.9 -> 2.7 culls/5 s;
> - kit song: 53-58 -> 13;
> - render-bound unison-8 song: 191 -> 109 (it still culls);
> - load unchanged, voices +0.1..+1.7.
>
> Needs more testing of render-bound songs before merging (one marginal overrun in about 53 s of the heaviest test
> song).

## Commands / tools

```sh
T=/home/user/perfwork/tools
$T/e1sim.py   runs/base-BU6-lean1                 # replay of setDireness/getRenderSize under the E1 rules (validates vs recorded)
$T/e1report.py runs/e1-abf-BU6-lean1 ...           # one-line results table rows
$T/e1neg.py [--below 4] runs/...                   # renders that ran the codec short, by dStep / doubled
$T/e1prederr.py runs/...                           # render-time prediction error by dStep
$T/e1dire.py runs/... --shift 0 10 20 30           # direness histogram with thresholds shifted
$T/e1hard.py runs/...                              # replay with hard cull independent of voice starts
```
Runs used `runbatch.sh` (copy elf/e1/probe-e1-<stage>.elf, set_song, run_emu_rings.py as in baseline.md). It's
copied to docs/perf/ticks/e1/ on the branch.
