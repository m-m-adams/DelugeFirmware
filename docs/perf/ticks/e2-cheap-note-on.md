# E2: cheaper note-on / note-off in the song tick, and a cheaper voice start

Worktree `/home/user/wt/e2-cheap-note-on`, off main 680b93c. **Status: stopped early by the coordinator** (see
"Unfinished" at the end). Everything below that is measured is from the emulator at Deluge speed
(`timectl=1,cachesim=0,cpi=1.45`, card `sd-baseline.img`), exactly as in `notes/baseline.md`.

## Commits (branch `perf/e2-cheap-note-on`, each builds on the previous one, clang-formatted)

| # | commit | what | files |
|---|---|---|---|
| c1 | 9d2a220 | arp: rebuild `notesByPattern` only when the PATTERN note mode reads it | modulation/arpeggiator.{h,cpp} |
| c2 | 9df58f4 | kit: take the drum's note row from the model stack instead of searching for it | model/instrument/kit.{h,cpp} |
| c3 | 9fdd021 | sound: don't schedule a song tick per pending note while the arp is off | sound_instrument.cpp, kit.cpp |
| c4 | ca6e99f | voice: cache the no-cables final values of local params per Sound | patch/patcher.{h,cpp}, sound.h, voice.cpp |

Measurement-only branches (not for merging):
- `perf/e2-cheap-note-on-probe` = c4 + probe (d954385). The cumulative builds c1/c2/c3 were c1/c2/c3 + probe e339b27.
- `perf/e2-notelog` = c4 + probe + 8e0bb70 "E2 note log": a `dzNoteLog` ring of every voice start and post-arp
  note-off per Sound (name hash, note, velocity), for comparing two builds note by note.
- `perf/e2-notelog-base` = main + probe + note log (the reference for that comparison). **Built: no** (stopped).

ELFs: `/home/user/perfwork/elf/e2/{c1,c2,c3,c4}-probe.elf` (+ song-patched copies `e2-<c>-<KEY>.elf`),
`n4-notelog.elf` (c4 + note log). Runs: `/home/user/perfwork/runs/e2-*`.

## Results

Per 16th step. Step tick = the tick that starts the step's notes (cullattr; samples of guest time, 1 sample = 6,255
instructions at cpi 1.45). "Tick/step ins" = PlaybackHandler::actionSwungTick instructions per step from full mode
(exact; includes the pending ticks where there still are any). Culls/5 s, load, voices from summarize.py (25 s
skip); min queued and chord latency from cullsim.py. All lean runs 200 slices; full runs 110 slices.

### Kits

| run | step tick (samples) | tick/step ins (full) | ticks/s | culls/5 s (hard/soft events per run) | load | voices | min queued | latency |
|---|---|---|---|---|---|---|---|---|
| BKIT20U4 base (lean1, lean2) | 26.7 | 204k | 32 | 58.0 / 52.6 (4-7 hard) | 86% | 18.0 | 72 | 13.2 ms |
| BKIT20U4 c1 lazy notesByPattern | 13.1 | - | 32 | 5.7 (0/55) | 87% | 18.8 | 85 | 13.1 ms |
| BKIT20U4 c2 + kit row index | 12.0 | - | 32 | 1.8 (0/17) | 87% | 18.9 | 87 | 13.1 ms |
| BKIT20U4 c3 + no pending ticks | 12.0 | - | **8** | 0.9 (0/9) | 87% | 18.9 | 87 | 13.3 ms |
| BKIT20U4 c4 + patching cache | **11.8** | **73.2k** | 8 | 3.5 (0/33) | 87% | 18.9 | 87 | 13.1 ms |
| KIT20 base (2 runs) | 28.8 | - | 32 | 1.2 / 1.2 | 69% | 19.3 | 78 | 10.5 ms |
| KIT20 c4 | **11.7** | - | 8 | **0.0** | 68-69% | 19.4 | 95 | 10.5 ms |

Single runs per build after the baseline. BKIT20U4's 0.9 -> 3.5 between c3 and c4 is noise at these counts (9 vs 33
soft events, c4 makes nothing in the tick more expensive; the step tick and every scope went down). Treat
"58 -> 1-6 culls/5 s, no hard events" as the result.

Per note in the kit (full mode, instructions per call; base = `runs/base-BKIT20U4-full-scopesB`, c3 =
`runs/e2-c3-BKIT20U4-full-scopesB`):

| | base | c3 |
|---|---|---|
| Kit::noteOnPreKitArp (incl. Sound::noteOn, and the 1 voice start in the tick) | 4,336 | 1,584 |
| Kit::noteOffPreKitArp (incl. Sound::noteOff) | 3,450 | 978 |
| of which kit arp Arpeggiator::noteOn / noteOff | (most of the rest) | 682 / 398 |
| Sound::noteOn / Sound::noteOff | 670 / 360 | 651 / 372 |

So the kit-level overhead per note went from ~3.7k (on) / 3.1k (off) to ~0.7k / 0.6k. What is left is the kit arp's
own bookkeeping (notes / notesAsPlayed insert, delete, search, randomizer) which runs even with the kit arp off; see
"Not done" for why I didn't skip it.

### Synth (BU6)

| run | step tick (samples) | tick/step ins (full) | ticks/s | culls/5 s | load | voices | min queued | latency |
|---|---|---|---|---|---|---|---|---|
| base (4 runs) | 11.0 | 86.7k | 32 | 8.6 / 16.0 / 12.9 / 7.3 (mean 11.2) | 87-88% | 18.7 | 84 | 13.1 ms |
| c1 | 9.2 | - | 32 | 6.2 | 88% | 18.8 | 86 | 13.1 ms |
| c3 (c1+c2+c3) | 9.2 | - | **8** | 5.9 | 88% | 18.8 | 85 | 13.2 ms |
| c4 (3 runs) | **8.9** | **55.8k** | 8 | 7.2 / 7.0 / 6.1 (mean 6.8) | 88% | 18.8 | 85-86 | 13.1 ms |

BU6 culls: every E2 run is below the baseline mean, all events soft; with BU6's ±40% noise call it "probably
better" (as E3 did). The tick per step is -36% in instructions (86.7k -> 55.8k): -1.8 samples on the step tick
itself (c1), plus the 3 pending ticks per step (each ~1.0 sample + a truncated render) gone (c3).

Per call, BU6 full mode (base `base-BU6-full(-scopesB)`, c4 `e2-c4-BU6-full(-scopesB)`):

| scope | base | c4 |
|---|---|---|
| Sound::noteOn (from the tick) | 1,377 | 995 |
| Arpeggiator::noteOn (arp off) | 813 | 505 |
| Sound::noteOff | 913 | 719 |
| Session::doTickForward per step | 84.3k (4 ticks) | 54.8k (1 tick) |
| Sound::noteOnPostArpeggiator (voice start) | 5,497 | 4,004 |
| Voice::noteOn | 4,676 | 3,182 |
| Patcher::performInitialPatching | 3,210 | 1,715 |
| Sound::process_postarp_notes (per call, every Sound every render) | 167 | 130 |

Kit voice start (BKIT20U4 full): noteOnPostArpeggiator 5,224 -> 3,721, Voice::noteOn 4,488 -> 2,994.

Unchanged, as expected: chord start latency (still 1 voice per render at direness 14; that's E1's territory),
direness (14 always), load.

## The changes, and why each is behaviour-preserving

### c1: lazy `Arpeggiator::notesByPattern` (biggest win; also E3's suggestion d3)

What: `rearrangePatterntArpNotes()` used to empty `notesByPattern` and re-insert every held note (one
`ResizeableArray::insertAtIndex` each) on every note-on and note-off, arp on or off, any note mode. Only
`switchNoteOn()` in `ArpNoteMode::PATTERN` reads it. Every kit note goes through the kit arpeggiator, so a 20-row kit
did 20 inserts per note-on and 19 per note-off: O(rows^2) per step, ~2k instructions per note each way.
Now `rearrangePatterntArpNotes()` copies `settings->notePattern` (16 bytes) and sets `notesByPatternStale`;
`switchNoteOn()` calls `rebuildNotesByPatternIfStale()` (the old body, reading the copied pattern) right before it
reads the array. `reset()` clears the flag (the array is emptied there).

Why identical: the rebuilt array is a function of (the `notes` array, the pattern at the time of the last rearrange
call). `notes` is only modified at the places that call rearrange (noteOn insert, noteOff delete, removeDrumIndex's
renumbering, reset), and the pattern is snapshotted at those calls, so whenever `switchNoteOn()` reads
`notesByPattern` it holds exactly what the eager rebuild would have left there, even if the user regenerated the
pattern or the settings object changed in between. Two edge differences, neither reachable in normal use:
- A null `settings` (MIDI/CV instrument with no active clip) used to make the rebuild read the pattern through a null
  pointer when notes were held; now the previous snapshot is used. The arp is off in that case anyway.
- On an allocation failure inside `noteOn()` (notes insert ok, notesAsPlayed insert failed, returns before the
  rearrange), a later lazy rebuild may include the half-inserted note where the eager code left the array one short.
Paths touched: every Arpeggiator/ArpeggiatorForKit noteOn/noteOff (synth, MIDI, CV, kit arps), PATTERN-mode arp steps.

### c2: kit row index from the model stack

What: `Kit::noteOnPreKitArp/noteOffPreKitArp` called `activeClip->getNoteRowForDrum(drum, &index)` (linear search) per
note. The sequencer (`NoteRow::processCurrentPos`, `InstrumentClip::sendPendingNoteOn`) and auditioning already put that
NoteRow and its index in the model stack. New `Kit::getNoteRowForDrumInActiveClip()` uses them if the model stack's
timeline counter is the active clip, the noteRowId is in range, the element at that index *is* the model stack's
NoteRow, and that row's drum is this drum; otherwise it falls back to the search.

Why identical: the checks prove the row at that index holds this drum; a drum has at most one NoteRow per clip
(the codebase relies on this everywhere, e.g. `getFirstUnassignedDrum`, model_stack.cpp's `getNoteRowForDrum` use),
so it is the row the search would return, with the same index. MIDI-in paths without a NoteRow in the stack take the
fallback. Saving: ~1.1 samples per kit step (13.1 -> 12.0).

### c3: no extra song tick per pending note with the arp off

What: `ArpeggiatorBase::doTickForward()` returns 0 ("come back next tick") while `handlePendingNotes()` finds a note
waiting for a voice, even with the arp off. With 1 voice start per render, every step was followed by 3 extra ticks
(+1, +2, +3 song ticks), each ~1 sample plus a render window cut short. For a `SoundInstrument` whose arp is off, and
for a kit `SoundDrum` whose drum arp is off, that 0 is now replaced by "no event" (2147483647). Ticks/s 32 -> 8.

Why it doesn't change what plays: with the arp off, the 0 only ever means "a note is pending" (the arp-off branch of
doTickForward returns 0 or 2147483647, nothing else). `Sound::render()` calls `getArp()->handlePendingNotes()` +
`process_postarp_notes()` on every render with the same settings object, and `reassessRenderSkippingStatus()` keeps
the Sound out of render-skipping while `hasPendingNotes()` (issue #2262 fix, already in main). Ticks and renders share
the same `allowedToStartVoice()` budget (reset per render, before the tick), so the extra tick only *moved* the
render's one start into the tick slot of the same render. Without it, the k-th pending note still starts in the k-th
render after the step (verified: chord latency 13.1-13.3 ms, 19 renders, same as base); what changes is that those
renders are no longer cut at +1/+2/+3 song ticks, so the render boundaries (not the starting render) move. Pending
starts have never been sample-accurate (ticksLate = samplesLate = 0). If a tick happens anyway, the tick still starts a
pending note as before.
Deliberately unchanged: arp-on Sounds (the 0 there also defers the arp's own step logic), the kit arpeggiator itself
(its pending notes, after switching the kit arp off with notes held, are only started from the tick: the kit's
render doesn't call handlePendingNotes with the arp off), and MIDI/CV instruments (`NonAudioInstrument::renderOutput`
ignores pending notes with the arp off, so the tick is the only thing that starts them).

### c4: per-Sound cache of the local params' no-cables final values (voice start)

What: `Patcher::performInitialPatching()` (58% of a voice start) computes, for every one of the 45 local params,
`conv_p(combine_nocables(value_p))` (a multiply, then a volume/linear/hybrid/exp conversion with table lookups), then
overwrites the params that have cables. Every voice of a Sound repeats this on the same values. New
`NoCablesFinalValues` in `Sound` (two `int32_t[LOCAL_LAST]` + a flag, 372 bytes per Sound): Voice::noteOn passes it,
and `performInitialPatchingWithCache()` recomputes a param's no-cables final value only if
`getSmoothedPatchedParamValue(p)` differs from the value it was cached for. Params with cables are combined per
voice as before and converted with `finalValueFromCableCombination()`, which is the same per-class conversion the old
code applied in its final passes. performInitialPatching 3,210 -> 1,715 instructions, voice start 5.5k -> 4.0k.

Why identical: the no-cables final value is a pure function of the param's value — `paramNeutralValues` and
`paramRanges` are written only in `functionsInit()` at boot, the conversions are pure — so a value-keyed cache can't
go stale; it's keyed on the exact input, not invalidated by events. Different ParamManagers for the same Sound
(one per clip) just miss and refill. Only the Voice patcher (config firstParam 0, endParams FIRST_GLOBAL) uses it;
the Sound's global patcher and the other callers pass nothing and run the old code. Cost: 372 bytes per Sound (47 KB
for a 128-row kit), and the per-param compare in every voice start (cheap).

## Behaviour checks

Done:
- All four builds boot, autoload and play BU6 / BKIT20U4 / KIT20 for 200 slices in the emulator with identical voice
  counts (18.8-19.4), identical chord latency and 1 start per render, 20 starts per step (probe counts:
  startedTick 8/s + startedPending 152/s on kits, same as base).
- Arp-ON test songs generated: `/home/user/perfwork/e2/gen_tickload_arp.py` (wraps gen_tickload.py, sets
  `arpMode="arp"` and a note mode on the clip arp), songs in `/home/user/perfwork/e2/songs/`: ARPPAT_U1 (5 synth clips,
  arp PATTERN mode — exercises c1's lazy rebuild), ARPUP_U1 (arp UP), ARPKITPAT (kit arp ON in PATTERN mode over 20
  rows). Card with them: `/home/user/perfwork/e2/sd-e2.img` (copy of sd-baseline.img + these songs).
- Unit tests: none of the firmware's unit/32-bit/spec test targets compile any of the changed files
  (tests/unit/CMakeLists.txt etc. list their sources explicitly: lfo, scales, sync, chords, memory, util...), so there
  is nothing in tests/ that covers these paths; not run.

**Not done (stopped):** running the arp-ON songs and the note-by-note comparison. The plan and tools are ready:
`run_emu_notelog.py` (run_emu_rings + periodic dump of `dzNoteLog`), `notelog_cmp.py RUN_A RUN_B` (per Sound: the
sequence of (start/off, note, velocity) must match; timing ignored). `n4-notelog.elf` (c4) is built;
`perf/e2-notelog-base` (main + probe + note log) still needs building. Songs to compare: BU6, BKIT20U4, ARPPAT_U1,
ARPUP_U1, ARPKITPAT (set dzSongPath with set_song.py to ARPPAT_U1 etc., card sd-e2.img, `--lean --slices 100`).

## Composition with E3 (a729ab9)

- c1 and c2 make the kit note-on *and note-off* path cheap. E3 removes refused note-ons from the tick but leaves
  note-offs (86% of the kit tick after E3), so c1+c2 are complementary: they shrink exactly what E3 leaves. With E3 the
  held-back note-ons still run Kit::noteOnPreKitArp in a later render; c1/c2 make that cheaper too.
- c3 overlaps E3's side effect (no pending ticks) for notes E3 holds back, but E3 only covers poly/arp-off/no-choke
  Sounds; c3 covers every arp-off Sound (mono/legato, choke drums, notes refused outside the tick path). They compose
  without conflict (different files: E3 touches instrument_clip/note_row/audio_engine/playback_handler; c3 touches
  sound_instrument.cpp and kit.cpp).
- c4 is independent of both (voice start), and benefits E3's deferred starts the same way.
- Not measured: c4 + a729ab9 (the coordinator's "if you have time"). Build recipe: c4 + a729ab9 + probe e339b27 +
  E3's probe hooks ed9e7b6.

## Not done / dropped (with reasons)

- Skip the kit arp entirely when it's off: **not behaviour-preserving**. With the kit arp OFF, the kit arpeggiator
  still (a) applies the arp randomizer to note-ons (note probability, velocity spread, reverse probability from the
  kit's unpatched params), (b) gates note-offs (a drum note-off is only sent if the drum index is in the kit arp's
  notes), and (c) keeps the held notes so that switching the kit arp on while notes are held arpeggiates them. After
  c1/c2 its remaining cost is ~680 / ~400 instructions per note-on / note-off (~3.5 samples per 20-note step).
  A fast path guarded by "mode OFF and all randomizer params neutral" would still have to keep (b) and (c).
- A "cheap pending-note record" instead of Arpeggiator::noteOn for refused synth starts: dropped. The synth
  arpeggiator's `notes` array is read by MPE (polyphonic expression lookups in sound.cpp / sound_instrument.cpp /
  melodic_instrument.cpp / midi_instrument.cpp), mono/legato note-off fallback, and arp-on-while-held. A second
  store would have to stay coherent with all of those. After c1, Arpeggiator::noteOn is 505 instructions (was 813);
  E3 removes the refused ones from the tick altogether.
- Further possible: skip `updateParamsFromUnpatchedParamSet` + empty-instruction processing in the per-tick
  `doTickForwardForArp` loops when the arp is off (kit: 20 drums per tick). Small after c3 (1 tick per step) and
  arpSettings fields are read elsewhere, so not done.

## Risks

- c1: the snapshot semantics must be kept if someone adds a new place that modifies `notes` — it must call
  rearrangePatterntArpNotes() (as before).
- c2: relies on "one NoteRow per drum per clip". If that invariant were ever broken, the old code used the first row
  and this would use the sequencer's row (arguably more correct).
- c3: relies on Sound::render starting pending notes with the arp off (true in main since the #2262 fix) and on
  reassessRenderSkippingStatus keeping pending Sounds rendering. If render-skipping logic changes, pending notes
  would wait until the next tick instead of the next render.
- c4: 372 bytes per Sound. The cache is only correct while the no-cables value is a pure function of the param
  value; if paramNeutralValues/paramRanges ever become runtime-variable, the key must include them.

## Draft upstream PR descriptions

**PR 1 — arp: only rebuild notesByPattern when PATTERN mode needs it (9d2a220)**
> `Arpeggiator::rearrangePatterntArpNotes()` rebuilt the pattern-ordered note list (one array insert per held note) on
> every note-on and note-off, whatever the arp mode, including with the arp off. Only the PATTERN note mode reads it.
> Kits run every note through the kit arpeggiator, so a kit playing 20 rows did ~800 array inserts per step.
> This keeps a copy of the pattern and rebuilds lazily in `switchNoteOn()`. Same list contents whenever it's read.
> Measured (emulator, Deluge speed): kit song with 20 rows per step: song tick 26.7 -> 13.1 samples per step; synth
> song with 20 notes per step: 11.0 -> 9.2 samples.

**PR 2 — kit: use the note row from the model stack in noteOn/OffPreKitArp (9df58f4)**
> Avoids a linear search over all note rows for every kit note on/off when the caller (sequencer, auditioning) already
> passed the row. Verified against the active clip; falls back to the search otherwise. 20-row kit: -1.1 samples per
> step.

**PR 3 — sound: no extra song ticks for pending notes with the arp off (9fdd021)**
> When a chord can't get all its voices at once (allowedToStartVoice() limits starts per render), the arp-off
> arpeggiator asked for a song tick on every tick until the chord had started (3-4 extra ticks per 16th step at high
> load). Each tick cuts a render short. Sound::render() already starts pending notes every render, so for Sounds with
> the arp off this ignores that request. Notes start in the same render as before. Song ticks per second: 32 -> 8 in
> the test songs.

**PR 4 — voice: cache local params' no-cables final values per Sound (ca6e99f)**
> Voice::noteOn's initial patching converted all 45 local params from scratch for every voice. The no-cables part is
> a pure function of each param's value, so it's now cached per Sound and keyed on that value. Identical
> paramFinalValues; voice start 5.5k -> 4.0k instructions (-28%), performInitialPatching -47%. Costs 372 bytes per
> Sound.

## Unfinished (when stopped)

1. Note-by-note comparison base vs c4 (note log) on BU6, BKIT20U4 and the arp-ON songs: base note-log build not
   made, no note-log runs. The arp-ON songs have not been played on any E2 build yet.
2. E2 c4 + E3 a729ab9 combined measurement.
3. Repeat runs for the kit per-change numbers (single runs only after the baseline).
4. BU8 (the "must still cull" control) not run.

---

# Resumed (2026-11-21): ported onto the deferred note-on work (fork main 3755636a2 = PR #18)

Branch `perf/e2-on-pno` in `/home/user/wt/e2-cheap-note-on`, rebased onto origin/main 3755636a2 (pushed as
`perf/ticks/claude-e2-on-main`; an earlier push of the same commits on 09d4d3a is `perf/ticks/claude-e2-on-pno` and
`perf/ticks/claude-perf/e2-on-pno`):

| commit (on main) | was | what |
|---|---|---|
| e21d8e8d2 | 9d2a220 | arp: rebuild notesByPattern only when the PATTERN note mode reads it (conflict in reset()/header with bc113ca's anyPending -> anyArpeggiated rename, resolved: keep `anyArpeggiated = false`, add `notesByPatternStale = false`) |
| ae3d48dab | 9df58f4 | kit: take the drum's note row from the model stack (clean) |
| f92479be2 | ca6e99f | voice: cache the no-cables final values of local params per Sound (clean) |

**9fdd021 dropped (obsolete):** bc113ca removed `ArpNoteStatus::PENDING`, `handlePendingNotes()`/`hasPendingNotes()` and
doTickForward's "return 0 while a note is pending". In main, `ArpeggiatorBase::doTickForward()` returns 0 only while
`takeNoteHeldThroughArpOff()` hands back notes held through an arp-off, which now happens *only* from ticks — so
overriding that 0 would break the hand-back. The pending ticks are already gone (8 ticks/s in every song).

Baseline for measurements: E3's `tbf-*` runs (09d4d3a + probe e81e0ec). main 3755636a2 differs from 09d4d3a only by
0f164f507 (note-editing UI, 2 lines in NoteRow::changeNotesAcrossAllScreens) and 601ee7ca8 (USB audio volume
control), neither on the tick/render path the benchmarks exercise.

## Note-by-note comparison (main 3755636a2 vs perf/e2-on-pno, all three commits)

Builds: `elf/e2/n0m-notelog.elf` = main + probe e81e0ec + note log; `elf/e2/n4m-notelog.elf` = perf/e2-on-pno (c1+c2+c4)
+ probe + note log. Lean, Deluge speed, 80 slices (~30 s of song) each, `run_emu_notelog.py`, runs
`runs/e2-nl-{n0m,n4m}-<song>`. Compared with `notelog_cmp.py` (per Sound sequence of voice starts (note, velocity) and
note-offs; global order of all starts and of all note-offs) and `notelog_steps.py` (per 16th step, multiset of
(Sound, start/off, note, velocity)).

| song (card) | what | per Sound sequences | global start order | global note-off order | per-step note sets |
|---|---|---|---|---|---|
| ARPUP_U1 (sd-e2) | 5 synth clips, arp UP | 5/5 identical | identical | identical | 252/252 identical |
| ARPPATF_U1 (sd-e2b) | 5 synth clips, arp PATTERN (fixed notePattern) | 5/5 identical | identical | identical | 250/250 |
| ARPKITPATF (sd-e2b) | kit arp ON, PATTERN, 20 rows | 19/19 identical | identical | identical | 256/256 |
| E3ARP (sd-e3) | synth arp in 3 clips | 3/3 | identical | identical | 237/237 |
| E3KARP (sd-e3) | kit arp | 8/8 | identical | identical | (identical) |
| E3DARP (sd-e3) | 8 drum arps | 8/8 | identical | identical | 232/232 |
| E3POLY (sd-e3) | mono/legato/auto/poly synths | 5/5 | identical | identical | 232/232 |
| E3KPOLY (sd-e3) | choke/mono/legato/auto/poly kit rows | 10/10 | identical | identical | 246/246 |
| BKIT20U4 (sd-e2) | kit 20 rows, unison 4, refused starts queue | 20/20 identical | differs within a step | identical | 256/256 |
| TICKLOAD_BU6 (sd-e2) | 5 synths x 4-note chords, unison 6 | 1/5 (see below) | differs within a step | identical | 264/264 |

ARPPAT_U1 / ARPKITPAT (the first versions, no notePattern in the song) differ, because the firmware generates a
random notePattern at load and the two builds' RNG state differs at that point: the fixed-pattern versions
(`gen_tickload_arp.py` now writes `notePattern="00000100030205010408020A06000D07"`) are identical.

In the two loaded chord songs, the same notes start and stop in every step, all note-offs are in the same order, but
the *order of starts within a step* differs: it's decided by the time budget (09d4d3a), which depends on how long the
tick and each voice start take, and both got cheaper. Starts moved by -300..+208 samples (mean -11 to -13, i.e.
slightly earlier); the first differences are two voices swapping places within one render (same start sample).
In BU6 this also reorders two notes of one Sound's chord now and then: the baseline itself does that (2 of 1128 starts
of one Sound start in descending note order within a render in main; 0 in E2), so it's the queue's timing-dependent
order, not an E2 logic change. Everything that doesn't depend on the voice budget (arp output, note-offs, kit rows,
mono/legato/choke behaviour) is identical event for event.

## Measurements on main (lean, Deluge speed, sd-baseline.img, 200 slices, 25 s skip; `tools/tb_report.py`, `cullattr.py`)

Before = E3's `tbf-*` runs (09d4d3a + probe e81e0ec; identical to main for these paths). A = main + arp + kit-row
(+ probe, `elf/e2/p2m-probe.elf`, runs `runs/e2-p2m-*`). A+B = perf/e2-on-pno: + voice-start cache
(`elf/e2/p4m-probe.elf`, runs `runs/e2-p4m-*`). All without #19 (E1).

| song | build | step tick samples mean/max | culls/5 s (hard) | voices | least queued | least time left | past empty / <4 | note delay mean | chord smear mean/max |
|---|---|---|---|---|---|---|---|---|---|
| KIT20 | before | 17.9/18.6 | 0 | 20.0 | 89 | 46.4 | 0/0 | 2.6 ms | 4.7/5.3 |
| | A | 10.8/11.5 | 0 | 20.0 | 96 | 55.7 | 0/0 | 2.1 | 4.4/4.9 |
| | A+B | **9.9/10.2** | 0 | 20.0 | 97 | 58.0 | 0/0 | 2.1 | 4.2/4.7 |
| BKIT20U4 | before (3) | 16.3/19.0 | 21.8/18.7/18.4 (0) | 19.3 | 81-82 | 14.3-16.5 | 0/0 | 4.8 | 7.1/8.3 |
| | A (2) | 9.3/11.5 | **0/0** | 19.7 | 87 | 27.4 | 0/0 | 4.5 | 6.8/8.0 |
| | A+B (3) | **8.6/10.6** | **0/0/0** | 19.6 | 88 | 27.8 | 0/0 | 4.5 | 6.7/7.8 |
| BU6 | before (3) | 8.4/11.5 | 10.4/10.7/9.8 (0.3-0.5) | 19.4 | 81 | 15.9 | 0/0 | 5.1 | 7.2/8.3 |
| | A (2) | 7.7/10.6 | 6.9/7.6 (0-0.1) | 19.4 | 81 | 16.4-16.9 | 0/0 | 5.0 | 7.2/8.3 |
| | A+B (3) | **7.8/9.7** | 7.2/6.4/6.9 (0) | 19.5 | 81 | 17.3 | 0/0 | 4.8 | 6.7/8.3 |
| BU8 | before (2) | 8.3/11.0 | 148.6/147.2 (5.2) | 16.5 | 73-80 | 6.2-9.0 | 0/0 | 5.3 | 7.9/9.5 |
| | A+B (2) | 7.5/9.7 | 148.6/147.7 (5.8-6.1) | 16.5 | 75 | 9.9-10.9 | 0/0 | 5.3 | 7.9/9.3 |

Load unchanged (69/87/88/89%). Step tick here includes the voices the time budget lets start in the tick.

Full mode (exact instructions per call, `runs/e2-tbf-*-full` = before, `runs/e2-p4m-*-full` = A+B):

| scope | BU6 before → A+B | BKIT20U4 before → A+B |
|---|---|---|
| PlaybackHandler::actionSwungTick (= tick per step, 1 tick/step) | 52.1k → 46.9k | **90.5k → 52.3k** |
| InstrumentClip::processCurrentPos | 9.9k → 8.9k | 86.7k → 48.5k |
| NoteRow::processCurrentPos (incl. note-offs) | 1,771 → 1,594 | 3,612 → 1,760 |
| Sound::noteOnPostArpeggiator (voice start) | 5,461 → 3,968 | 5,459 → 3,925 |
| Voice::noteOn | 4,628 → 3,134 | 4,440 → 2,948 |

## Memory cost of the voice-start cache (f92479be2)

sizeof(SoundDrum) 2,024 → 2,392 bytes (+368; `sampleForPreviewMemory` symbol size in the ELFs). Sounds come from
`GeneralMemoryAllocator::allocMaxSpeed()`, which tries the internal SRAM heap first (`__heap_start` 0x201736c4 ..
`program_stack_start` 0x202f8000, ~1.52 MB) and falls back to SDRAM. 128-row kit: +47 KB; 20-row kit: +7 KB.
Saving: ~1.5k instructions (~0.24 samples) per voice start, i.e. ~30% of a start, wherever starts run (tick and
queue); BU6 chord smear 7.2 -> 6.7 ms. Recommendation: worth it for the smear/tick, but it's the reviewer's call on
internal RAM; a variant could allocate the table lazily in SDRAM (one allocation on a Sound's first note, which E4
would want out of the audio routine), or key only the params without cables.

## PRs (into perf/performance-review, base main 3755636a2)

- #20 https://github.com/m-m-adams/DelugeFirmware/pull/20 — head `perf/ticks/claude-e2-arp-kit-review`
  (410775d2f arp lazy notesByPattern, 128a3bdaa kit note row from the model stack).
- #21 https://github.com/m-m-adams/DelugeFirmware/pull/21 — head `perf/ticks/claude-e2-voice-cache-review`
  (617179e6a voice-start cache), independent of #20 (applies and builds on main alone; measured stacked on #20).

Both review branches build plain (no probe) on main. Not tested: hardware, MIDI input, MIDI/CV arps in PATTERN mode,
deleting kit rows while playing, memory pressure with huge kits.

Observation for E3/#18 (not E2): in main, a Sound's chord notes occasionally start in descending note order within one
render (2 of 1128 starts of one BU6 Sound) — the queue's per-Sound order isn't strictly tick order. Same start sample,
so inaudible.
