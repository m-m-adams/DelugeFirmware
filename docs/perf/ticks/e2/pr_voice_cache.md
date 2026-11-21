## Summary

**voice: cache the no-cables final values of local params per Sound**

`Patcher::performInitialPatching()` was more than half of every voice start: 3.2k of the ~5.5k instructions of `Sound::noteOnPostArpeggiator`. For each of the 45 local params, it converts the param's own value into a final value as if no cable were patched to it: a multiply, then the volume / linear / hybrid / exp conversion with table lookups. Then it overwrites the params that do have cables. Every voice of a Sound repeats that conversion on the same values.

That no-cables final value is a pure function of the param's (smoothed) value: `paramNeutralValues` and `paramRanges` are only written in `functionsInit()` at boot. So `Sound` now keeps a `NoCablesFinalValues` table: the input value and the final value per local param, plus a flag. `Voice::noteOn()` passes it in. A param's final value is only recomputed when its current value differs from the cached input.

Because the cache is keyed on the exact input rather than invalidated by events, it can't go stale. A Sound used with several ParamManagers (one per clip) just misses and refills. Params with cables are still combined per voice, then converted by `finalValueFromCableCombination()`, the same per-class conversion the old final passes applied. `paramFinalValues` come out identical. Only the voice patcher uses the cache; the Sound's global patcher and the other callers run the old code.

**Cost: 368 bytes per Sound** (sizeof(SoundDrum) goes from 2,024 to 2,392 bytes). Sounds are allocated with `allocMaxSpeed()`, which tries internal SRAM first (about 1.5 MB internal heap). A 128-row kit therefore uses about 47 KB more, in internal RAM while it has room and in SDRAM after that. That's the trade-off to judge here.

### Measurements

Emulator (delugemu) at Deluge speed (`timectl=1,cachesim=0,cpi=1.45`), 200-slice lean runs plus 110-slice full-mode runs for exact instruction counts.

**Measured stacked on the arp/kit PR**: before = that PR + probe, after = that PR + this commit + probe. Both are on main 3755636a2, **without #19**. This commit applies to main on its own and builds there, but it wasn't measured alone. Its effect is independent of the other PR: it only touches Voice::noteOn's patching.

| | before | after |
|---|---|---|
| `Sound::noteOnPostArpeggiator` (voice start), instructions per call, BU6 / BKIT20U4 | 5,461 / 5,459 | **3,968 / 3,925** |
| `Voice::noteOn`, BU6 / BKIT20U4 | 4,628 / 4,440 | 3,134 / 2,948 |

In the first row, before is main + probe (full mode). On the pre-#18 code, `performInitialPatching()` itself went from 3,210 to 1,715 instructions per call.

| song (lean, runs before → after) | chord smear mean / max | mean note delay | culls / 5 s (hard) | least queued | least time left | renders past empty |
|---|---|---|---|---|---|---|
| KIT20 (1 → 1) | 4.4 / 4.9 → 4.2 / 4.7 ms | 2.1 → 2.1 ms | 0 → 0 | 96 → 97 | 55.7 → 58.0 | 0 → 0 |
| BKIT20U4 (2 → 3) | 6.8 / 8.0 → 6.7 / 7.8 ms | 4.5 → 4.5 ms | 0 → 0 | 87 → 88 | 27.4 → 27.8 | 0 → 0 |
| BU6 (2 → 3) | 7.2 / 8.3 → **6.7 / 8.3 ms** | 5.0 → 4.8 ms | 7.2 → 6.8 (0 hard) | 81 → 81 | 16.4-16.9 → 17.3 | 0 → 0 |
| BU8 (main 2 → stack 2) | 7.9 → 7.9 ms | 5.3 → 5.3 ms | 148 → 148 (5.2 → 5.8-6.1 hard) | 73-80 → 75 | 6.2-9.0 → 9.9-10.9 | 0 → 0 |

The time budget (#18's third commit) charges each start its measured cost. Cheaper starts mean slightly more of a step's chord starts in the renders right after the step, so the smear shrinks a little. The voice starts themselves now cost about 30% less wherever they run: in the tick, and from the queue in the renders. Load, voices and culls are unchanged within noise.

### Behaviour check

The note-by-note comparison (voice starts and note-offs per Sound, logged in the firmware) of main against the arp/kit PR stacked with this commit was identical in every song that doesn't use the voice budget. That covers arp, kit arp, drum arps, mono/legato/poly and choke rows. In the loaded chord songs, the same notes started and stopped in every step; only the within-step order of starts moved with the time budget. See the arp/kit PR for details.

## Type of change

- [ ] Bug fix
- [ ] New feature or changed behavior
- [x] Refactor (no behavior change)
- [ ] Chore (build, CI, docs, tooling)

## What to test

- Sounds with patch cables to local params (envelopes / LFOs / velocity / note → cutoff, pitch, wave index, pan, env times): new notes should sound exactly as before.
- Changing a param (knob, automation, MIDI learn) between notes: the next note has to pick up the new value. The cache is keyed on the value, so a changed value is a miss.
- The same synth in several clips with different param values.
- Large kits: watch free RAM.

Untested:
- hardware;
- memory pressure with very large kits / many synths (internal RAM running out earlier);
- songs with automation on many local params.

What to look at in review:
- `Patcher::performInitialPatchingWithCache()` against the old body. It's the same conversion, but split per param instead of per class pass.
- The 368 bytes per Sound. If that's too much in internal RAM, the table could live elsewhere (lazily allocated in SDRAM), at the cost of an allocation on a Sound's first note.

Notes and run data: [`docs/perf/ticks/e2-cheap-note-on.md` on perf/ticks/claude-e2-on-main](https://github.com/m-m-adams/DelugeFirmware/blob/perf/ticks/claude-e2-on-main/docs/perf/ticks/e2-cheap-note-on.md).

## Checklist

- [ ] I tested this on hardware (or it cannot affect firmware behavior)
- [ ] It works on both OLED and 7SEG
- [ ] User-visible changes have an entry in `docs/community_features.md`
- [ ] Behavior changes are behind a runtime setting (or this is not a behavior change)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_011tnhfEs1tfKLppNdGBRRup
