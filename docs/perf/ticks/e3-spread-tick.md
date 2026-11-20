# E3: spread the song tick

Worktree `/home/user/wt/e3-spread-tick`, off main 680b93c. Local commits only.

| branch | commit | what |
|---|---|---|
| `perf/e3-spread-tick` | **a729ab9** | "Hold back sequenced note-ons that can't get a voice from the song tick". The E3 change, one commit, clang-formatted, builds clean |
| `perf/e3-spread-tick-probe` | a729ab9 + 2f64aa7 (probe e339b27, cherry-picked clean) + ed9e7b6 | Measurement branch. ed9e7b6 adds two probe hooks so held-back notes count like refusals in the tick, and their starts count like pending starts. Not for merging |

ELFs:
- `/home/user/perfwork/elf/e3-spread-tick.elf`: plain E3, no probe.
- `/home/user/perfwork/elf/e3-spread-tick-probe.elf`: measurement build. `e3-{BKIT20U4,D_KIT20,BU6,BU8}.elf` are copies with the song patched.
- `/home/user/perfwork/elf/e3/v1-*.elf`: the first measurement build (`e3-v1-probe.elf`). It's functionally identical; the
  final commit only reordered a validity check in `send()`, see below.

## TL;DR

- **The change:** when the song tick would send a note-on that `allowedToStartVoice()` is going to refuse anyway, the
  note isn't sent. It goes into a small queue (`DeferredNoteOns`, 64 entries). This only applies to notes for a Sound
  that just plays the note: polyphonic, arp off, not a choke drum. Every render, right after the tick slot and before
  the song renders, sends as many queued notes as can start a voice, oldest first. That is the render that would have
  started the note from pending anyway, so voices start no later than before. MIDI/CV/gate outputs never take this
  path, so their timing is unchanged.
- **Kits** (where the tick is big), per 16th step:
  - The step tick drops from 28.8 to 15.2-15.8 samples (KIT20) and from 26.7 to 14.7-16.3 (BKIT20U4). For
    BKIT20U4 that is 204k -> 103k instructions per step, exact in full mode.
  - songTickCost drops from 29 to 16 and from 27 to 17.
  - Culls: KIT20 1.2 -> 0 per 5 s (2 runs each). BKIT20U4 58.0 / 52.7 -> 15.1 / 11.8 per 5 s, about -76%, with hard
    events 69 / 43 -> 0 / 0.
  - The render after the step (dStep 1) starts with 96 queued instead of 83 (KIT20), and 87.5 instead of 78
    (BKIT20U4).
- **Synths (BU6):**
  - The step tick drops from 11.0 to 7.6 samples, and songTickCost from 11.2 to 7.9.
  - Culls/5 s are 6.1 / 7.0 / 6.2 / 5.7 (mean 6.3), against the baseline's 8.6 / 16.0 / 12.9 / 7.3 (mean 11.2), and
    there are no hard events. Every run is below the baseline's lowest, but BU6's noise is ±40%, so call it
    "probably better".
- **BU8** (genuinely overloaded): 191.8 -> 152.0 culls/5 s, hard events 755 -> 132, voices 14.9 -> 16.0. It still
    culls, as it should.
- **Side effect: the pending-note ticks are gone.** Ticks/s drop from 32 to 8 (one per step).
  `ArpeggiatorBase::doTickForward()` asks for a tick on every song tick while a note is pending in an arp. Held-back
  notes are not in the arp until they start, and once a note starts, it starts right away. So those 3-4 extra ticks
  per step, and the render cuts they cause, no longer happen for the notes this path covers. E2 is fixing the same
  thing for all notes. Both can go in.
- **Unchanged:** chord start latency (still 1 voice per render at direness 14: 10-13 ms, the same as before), load,
  voices (except BU8, +1.1 because fewer culls), and the direness histogram (still 14 always; that is E1's job).
- **Overlap with E2:** E2 is making refused starts and the kit note-on path cheaper. This change removes most of that
  work from the tick altogether, by moving it to the renders where the voice actually starts. The two compose.
  After E2, the gain from this change shrinks to whatever a refused note-on still costs. The remaining kit tick is
  86% note-offs (Kit::noteOffPreKitArp). Those must stay in the tick for timing; E2's kit fix should shrink them.

## 1. The tick path (main 680b93c)

How it runs:
- `AudioEngine::routine_()`: setDireness() (culls), getRenderSize(), `voices_started_this_render = 0`, then
  `tickSongFinalizeWindows()`, then renderAudio().
- `songTickCost.add()` takes the time from the start to the end of tickSongFinalizeWindows(). It's the most over 2 s,
  in 8 x 0.25 s slots, and setDireness() and getRenderSize() reserve it in **every** render.
- tickSongFinalizeWindows():
  - It actions every timer/swung tick due at or before `audioSampleTimer`, in a loop
    (`actionTimerTick` / `actionSwungTick` + `scheduleSwungTick`).
  - It **cuts the window short** so the next tick lands exactly on a render boundary
    (`numSamples = timeTilNextTick`). A tick therefore always runs at the very start of a render, and everything
    it does takes effect at sample 0 of that render.
  - It captures gates at offset 0, and MIDI is "due at window offset 0".
  - Mid-window trigger and MIDI clock-out ticks get their own offsets.
- `actionTimerTick` advances the timer tick and actions any swung ticks up to it. Then `actionTimerTickPart2`
  schedules the next timer tick (every 2 swing intervals), the swung tick, LFO resync, and clock-out scheduling.
- `actionSwungTick`:
  - count-in;
  - `considerLaunchEvent()`, which increments every clip's position and may launch clips or swap songs;
  - `Session::doTickForward()`:
    - every clip's `processCurrentPos()`: param automation, then `InstrumentClip` -> each `NoteRow::processCurrentPos()`;
    - then `doTickForwardForArp()` on every output;
  - the metronome.
  - It keeps `swungTicksTilNextEvent` at the minimum over everything that wants to be revisited, and that sets
    when the next swung tick is scheduled.
- `NoteRow::processCurrentPos()`:
  - **Note-offs go out immediately** (`stopCurrentlyPlayingNote` -> `playNote(false)` -> instrument / kit -> `Sound::noteOff`).
  - Note-ons are collected in the static `PendingNoteOnList`. InstrumentClip then applies probability, iterance and fill,
    and calls `sendPendingNoteOn()`.
- `sendPendingNoteOn()` -> `MelodicInstrument::sendNote` / `Kit::noteOnPreKitArp` -> `Sound::noteOn`. Sound::noteOn:
  - checks the sources;
  - runs `ArpeggiatorSettings::updateParamsFromUnpatchedParamSet`;
  - calls `arpeggiator->noteOn`, which does the bookkeeping even with the arp off: notes, notesAsPlayed, the
    notesByPattern rebuild, and the randomizer;
  - asks `AudioEngine::allowedToStartVoice()` for each post-arp note. It allows 4 per render, or 1 when
    cpuDireness > 12, which is always at these loads. If allowed, it calls `noteOnPostArpeggiator` -> `Voice::noteOn`.
    If refused, the arp note stays PENDING;
  - finally calls `reassessRenderSkippingStatus`.
- A pending note starts from `Sound::render()`: `handlePendingNotes` -> `process_postarp_notes`, one per Sound per
  render, under the same per-render cap, **with ticksLate = samplesLate = 0**. It also starts from
  `doTickForwardForArp`. That is because `ArpeggiatorBase::doTickForward` returns 0 while anything is pending, which
  makes the session schedule a swung tick on every song tick until the chord has started. These are the "pending
  ticks", 3-4 per step.

`samplesLate` is only used by sample oscillators (they skip ahead). Synth oscillators and envelopes don't
compensate, so starting a voice one render late moves it by one render. `ticksLate` is only used for
time-stretch sync (`sequenceSyncStartedAtTick`).

What has to stay sample-accurate:
- MIDI notes and clock, CV/gate, and trigger clock. They're stamped with the window offset when they're produced (the
  tick is at offset 0).
- Note-off timing (gate length), and note-on timing for every voice that is allowed to start.
- Mono/legato voice handover, the choke groups (`Kit::choke()` at note-on), and arpeggiator sync, which reads the
  arp's note list in the same tick.
- Song swaps, clip launches, count-in and external clock (all in actionSwungTick and considerLaunchEvent).

Where the step tick's time goes, from the baseline (exact, full mode):
- BU6: 86.7k instructions per step, of which the step tick is 67.6k = 10.8 samples. Note-ons are 32%, and 19 of
  the 20 are refused (24%). Then note-offs 21%, arp tick-forward 21% (the pending ticks), row scan 17%.
- Kits: 204k per step, of which the step tick is 165k = 26.7 samples. The note-on path (Kit::noteOnPreKitArp,
  20 x 4.34k) is 43%, and 19 of the 20 notes are refused. The note-off path (Kit::noteOffPreKitArp, 20 x 3.45k) is
  34%.

## 2. Options considered

| option | payoff | risk | verdict |
|---|---|---|---|
| (a) Lookahead: scan the next tick's note rows in the renders before it (or in a low-priority task) and queue timestamped events | Only the scan itself (7-17% of the step tick) can be done ahead without side effects. Note-ons and note-offs mutate arp, voice and NoteRow state, and must happen at the tick. The renders before a step are the most loaded of all (full chord sounding, margin 43-49), so moving work there moves it from one tight render to another | High: needs a side-effect-free copy of NoteRow::processCurrentPos (reverse, ping-pong, independent row lengths, drones, auditioning, probability/iterance/fill, recording). It must be invalidated on every edit, mute, launch, tempo or swing change | rejected |
| (b) Cheap refusal: don't pay the note-on path for notes that will be refused; do the work when the voice can start | The refused note-on path is 24% (synth) to ~40% (kit) of the step tick, and that work then happens in the renders after the step, which are the cheapest in the song (old voices gone, new ones trickling in at 1 per render; baseline dStep 2-20 never culls) | Low if limited to Sounds where nothing depends on the exact send order (no arp, poly, no choke). It's still an exact match with today's "pending" semantics: the voice starts in the same render as before | **implemented** |
| (c) Split the tick by clip (round robin with late-start compensation) | Could split note-offs too | Note-offs and note-ons of deferred clips move by a render (`samplesLate` doesn't compensate synth voices). `swungTicksTilNextEvent` has to be known at the tick to schedule the next one, so deferred clips would need rescheduling. considerLaunchEvent has already moved positions. MIDI/CV clips would have to be excluded | rejected |
| (d1) Action a tick at the *end* of the render it cuts, instead of at the start of the next | Zero timing change for audio: the next sample after the cut render *is* the tick. MIDI/gates can be stamped at offset numSamples of the cut window. songTickCost would no longer include it, since its cost shows up in the queue the next setDireness() sees | Measured the gap: after a cut (short) render, space is already above the routine threshold, so the next routine_ starts immediately (`space(d0) = space(d-1) - samples(d-1) + time(d-1)` holds in the ring). So it saves no real time; it only changes the accounting, which is E1's job. It also reorders user actions in that gap relative to the tick (up to 1 render) and needs care with external clock | rejected (documented as an E1 alternative) |
| (d2) Note-offs a render early, or deferred note-off bookkeeping | Note-offs are now 86% of the kit tick | Early note-offs change gate lengths. Deferring only the bookkeeping (kit arp lists) has to stay ordered with the next note-on, and E2 is removing that bookkeeping when the kit arp is off | left to E2 |
| (d3) Lazy `Arpeggiator::notesByPattern` rebuild (only needed when the arp runs in PATTERN mode, but rebuilt with O(n) inserts on every note-on and note-off) | ~5-10% of note-on/off bookkeeping | Low | micro-optimisation, suggested to E2, not done here |

## 3. Implementation (a729ab9)

New: `src/deluge/model/clip/deferred_note_ons.{h,cpp}` (namespace `DeferredNoteOns`).
- `tryDefer(clip, pendingNoteOn)` is called by `InstrumentClip::processCurrentPos` instead of `sendPendingNoteOn`,
  after probability, iterance and fill have passed. It queues the note only when all of these hold:
  - `AudioEngine::wouldRefuseVoiceStart()` (new: the cap has been reached in this render; it doesn't count a start);
  - the queue isn't full (64);
  - the target is a Sound that just plays the note: a SYNTH with `polyphonic == POLY` and the clip arp off, or a
    KIT row whose drum is a SoundDrum with the clip (kit) arp and the drum arp off and polyphony not CHOKE;
  - `output->inValidState`.

  A queued note stores the clip, output, NoteRow pointer (compared, never followed blindly), drum, noteRowId,
  velocity, sampleSyncLength, ticksLate, and the swung tick it was due on.
- `sendWhatCanStart()` is called in `routine_()` right after the tick slot (`renderStartTicks` is taken first, so the
  time counts as render, like the pending starts it replaces, and not as songTickCost), and also in the offline
  stem-export loop. While `!wouldRefuseVoiceStart()`, it takes the oldest note and checks it is still valid:
  - the clip is in currentSong (session or arrangement-only array);
  - same output, and the output's activeClip is this clip;
  - the NoteRow is found among the clip's rows by pointer, with the same id and drum, not muted;
  - it is still a "just plays" Sound.

  Then it sends the note through `InstrumentClip::sendPendingNoteOn` (made public), with ticksLate advanced by the
  swung ticks since. Sound::noteOn then starts the voice (the cap allows it), and the arp sees the note at the same
  moment the voice starts.
- Dropping queued notes:
  - `NoteRow::stopCurrentlyPlayingNote` calls `forgetNoteRow` (a note-off of a note that never started: the same as
    today, where the arp's noteOff deletes the pending note). This covers note ends, clip stop (expectNoFurtherTicks
    -> stopAllNotesPlaying), row mute, and note deletion while playing.
  - `~InstrumentClip` calls `forgetClip`.
  - `PlaybackHandler::endPlayback` calls `forgetAll`.
  - `sendWhatCanStart` forgets everything if no clock is active.

Size: +290 lines (196 in the new .cpp, mostly comments and validation), and 33 lines changed in five existing files.
No per-render cost when the queue is empty: one load and compare.

## 4. Correctness and timing: what can move, and by how much

- **Voices that start in the tick:** unchanged. A note is held back only when the cap would refuse it.
- **Voices that were refused:** today they start from pending in Sound::render of a later render, in Sound render
  order, 1 per Sound per render. Now they start in that same render's slot, just before the song renders, oldest
  first. With the cap at 1 (direness 14), the k-th refused note of a step starts in render k after the step, both
  before and after. With the cap at 4, the k-th starts in render ceil(k/4), both before and after. The difference is
  which note goes first: tick order (clip by clip, row by row) instead of Sound render order. Both start at sample 0
  of their render, with ticksLate as before (pending starts used 0; now it's the true offset, which matters only for
  time-stretch sync). Measured chord start latency: BU6 13.1 -> 13.2 ms, KIT20 10.5 -> 10.4 ms, BKIT20U4
  13.2 -> 12.9 ms.
- **Note-offs:** unchanged, still in the tick. A note-off for a held-back note removes it, the same as today.
- **MIDI / CV / gate:** unchanged. Non-Sound outputs are never held back, and neither is their MIDI out. A Sound with
  `outputMidiChannel` sends its MIDI note when the voice starts (`noteOnPostArpeggiator`), the same render as
  before.
- **Choke, mono/legato, arpeggiator (synth, kit and drum):** never held back, so their ordering and arp sync are
  unchanged.
- **Kit arp randomizer** (runs even with the kit arp off): the same calls in the same order, made later.
- **Render-skipping:** a Sound with no voices and a held-back note may stay skipped until the note is sent. Then
  noteOnPostArpeggiator's `reassessRenderSkippingStatus` wakes it, before it renders.
- **Edits while a note is queued** (a few ms at most, about 1 ms per queued note at direness 14): row
  deleted/moved, instrument changed, clip deleted or stopped, song swapped. The note is dropped, never sent to the
  wrong place.
- **Pending ticks:** held-back notes aren't pending in an arp, so `doTickForward` no longer forces a tick per song
  tick for them. The song schedules exactly the ticks its events need (8/s instead of 32/s here). No timing changes:
  those ticks only started pending notes, which now start in the same render as before.
- **songTickCost / getRenderSize / setDireness:** untouched (E1 owns them). The tick is cheaper, so songTickCost follows
  automatically (KIT20 29.3 -> 15.5 samples). The note-on work moved into renders shows up in renderCost like the
  pending starts did, about 0.7 extra samples per held-back note on kits, in renders that have room.

## 5. Measurements

All runs: lean mode at Deluge speed, `run_emu_rings.py` (merged ring), `sd-baseline.img`, 200 slices. Load,
culls/5 s and voices come from `summarize.py` (25 s skip). Step tick, songTickCost, dStep1 queued and cull events come
from `cullattr.py`. Latency, least queued and least time left come from `cullsim.py`. Baseline runs are the
baseline agent's (`runs/base-*`). E3 runs are `runs/e3-v1-*` (v1 build) and `runs/e3-*-lean2/4` (final build).

Per 16th step. "Step tick" is the tick that starts voices (cullattr, ns -> samples). "dStep1" is the render right after
the step-tick render; it has the least margin of the step. Cull events are hard/soft per run (about 50 s of audio).

| song | run | load | culls/5 s | cull events hard / soft | voices | step tick mean / max (samples) | songTickCost | ticks/s | step-render tick ns | dStep1 queued / margin | dStep1 render ns (samples) | culls at dStep 0 / 1 | chord latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **KIT20** | base lean1 / lean2 | 69% | 1.2 / 1.2 | 0/12, 0/12 | 19.3 | 28.8/29.6, 24.9/25.6 | 29.3, 25.5 | 32 | 654k, 565k | 83.2 / 9.3 | 817k (64.9) | 0 / 100% | 10.5 ms |
| | **E3** v1 / final | 68-69% | **0.0 / 0.0** | 0/0, 0/0 | 19.4 | **15.2/15.9, 15.8/16.3** | **15.5, 16.3** | **8** | 346k, 359k | **96.3 / 40.7** | 766k (58.4) | - | 10.4 ms |
| **BKIT20U4** | base lean1 / lean2 | 86% | 58.0 / 52.7 | 69/418, 43/471 | 18.0 | 26.7/27.4, 26.2/26.9 | 27.2, 26.7 | 32 | 605k, 594k | 78.0 / 7.1 | 1075k (61.3) | 25% / 26% (+15% d2, 34% 11+) | 13.2 ms |
| | **E3** v1 / final | 86% | **15.1 / 11.8** | **0/156, 0/120** | 18.7 | **16.3/16.8, 14.7/15.0** | **16.7, 15.0** | **8** | 370k, 333k | **87.5 / 17.4**, 88.9 / 21.6 | 1311k (74.3) | 46-12% / 54-88% (none at d2+, none 11+) | 12.9, 12.7 ms |
| **BU6** | base lean1 / lean2 / hot / full | 87-88% | 8.6 / 16.0 / 12.9 / 7.3 (mean 11.2) | 4/76, 8/137 | 18.7 | 10.8/11.0, 11.0/11.5 | 11.0-11.4 | 32 | 245-250k | 89.8 / 21.5-23.8 | 1357-1372k (71) | 39-64% / 32-52% | 13.1 ms |
| | **E3** v1 lean1(ring only) / v1 lean2 / v1 lean3 / final lean4 | 87-88% | **6.1 / 7.0 / 6.2 / 5.7** (mean 6.3) | -, 0/68, 0/60, 0/55 | 18.8 | **7.6/8.0** | **7.9** | **8** | 172k | 93.3 / 32 | 1283k (64.5) | 56-75% / 25-44% | 13.2 ms |
| **BU8** (overload control) | base lean1 / lean2 | 87-88% | 191.9 / 191.6 | 755/413, 768/438 | 14.9 | 11.0/11.5 | 11.4 | 32 | 250k | 91.4 / 28.9 | 1266k (67.7) | 5% / 3% (92% at 11+) | 14.1 ms |
| | **E3** final | 89% | **152.0** | **132/1352** | **16.0** | 7.5/8.0 | 7.9 | 8 | 170k | 91.4 / 28.4 | 1364k (68.2) | 11% / 2% | 14.6 ms |

Other:
- Least queued at any render start: KIT20 78 -> 92, BKIT20U4 72 -> 82/84, BU6 84 -> 86, BU8 73 -> 84 samples.
- Least time left in the codec buffer when a render finished: KIT20 43 -> 54, BKIT20U4 8.8 -> 16.4/18.4, BU6 13.2 -> 17,
  BU8 4.7 -> 10.9 samples.
- Direness: still 14 in 100% of renders in every run, so voice starts stay at 1 per render. That's for E1.
- N30 (70%, no culls; ring-only runs): step tick 17.0 -> 11.3 samples, songTickCost 17.8 -> 11.8, ticks/s 32 -> 8.
- Noise: KIT20 and BKIT20U4 repeat within 10-25%. The E3 BKIT20U4 runs (15.1, 11.8) are far outside the baseline's
  52.7-58.0. BU6's 4 E3 runs (5.7-7.0) are all below the baseline's lowest (7.3), but BU6's noise is ±40%. BU8:
  -21% culls, and hard events 1/6 of baseline, with more voices kept (16.0 vs 14.9). It still culls heavily, as an
  overloaded song should.
- Where the cull moved in BKIT20U4: the baseline culled at dStep 0, 1 and 2, and in steady state (11+, 34%). E3 culls
  only at dStep 0/1, and only soft. The remaining culls are prediction artefacts (cullsim: E1's tick-only
  reservation + minimum size removes them; see baseline.md).

Exact tick cost (full mode, plugin SCOPE, per 16th step), BKIT20U4:

| | baseline (`base-BKIT20U4-full`) | E3 (`e3-v1-BKIT20U4-full`) |
|---|---|---|
| PlaybackHandler::actionSwungTick calls per step | 4 (1 step + 3 pending ticks) | 1 |
| tick instructions per step | 204k (32.6 samples) | **103k (16.5 samples)** |
| NoteRow::processCurrentPos (incl. note-offs) per step | 20 x 4.21k = 84k | 20 x 4.44k = 89k |
| note-on work in the tick | 19 refused + 1 started (~92k) | 1 started (~10k) |
| Sound::process_postarp_notes per call | 48 ins | 10 ins (nothing pending) |

What the remaining kit tick is: ~89k of the 103k is note-offs + row scan (Kit::noteOffPreKitArp ~3.1k kit overhead per
note on top of Sound::noteOff). That is E2's kit fix (skip the kit arp when it's off, use the row index).

## 6. Risks

- Order of refused starts within a step changes (tick order instead of Sound render order). With the cap at 1, chord
  notes trickle in either way. Notes of one clip now complete before the next clip's start, where before each Sound
  started one note per render in turn.
- A note held back past a structural edit is dropped, never sent wrongly. The checks are a linear search over the
  song's clip arrays and the clip's rows, per sent note. That's trivial for normal songs. For a song with hundreds of
  clips it costs a few hundred instructions per held-back note.
- A one-shot (no-tail) held-back note whose clip is stopped within the few ms it's queued can still start. That's the
  same as today's pending one-shot, which also starts after the stop.
- The pending-tick removal depends on held-back notes not being in the arp. Notes that aren't eligible (arp on, mono,
  choke, MIDI/CV) still cause pending ticks until E2 lands.
- Interaction with E1: E1 will reserve songTickCost only in tick renders. This change makes that reservation smaller
  (16 instead of 27-29 samples for kits). There are no textual conflicts with setDireness/cullVoices/getRenderSize.
  It touches `allowedToStartVoice()` (refactor plus the new `wouldRefuseVoiceStart()`) and one line in routine_().
- Interaction with E2's "cheap refused starts": both touch the refused-note path. If E2 makes refusal nearly free,
  this change still removes the kit note-on overhead from the tick (unless E2 removes that too) and the arp
  bookkeeping. Whichever lands second should re-measure.

## 7. Upstream readiness

- Self-contained, one commit, clang-formatted, builds clean (Release). The unit tests don't compile these files.
- The commit message and comments explain the why. No behaviour change at low load, because nothing is held back
  while voices can start.
- Before a PR:
  - Hardware check: kit and synth songs at high load. Edit while playing: delete rows, change the kit drum, swap
    songs, stop and start, record, arranger playback.
  - Test that MIDI-out on a Sound (outputMidiChannel) still sends.
- Possible follow-ups:
  - Also hold back notes that *are* allowed but would start in the tick render. The voice start (5.5k
    instructions, 0.9 samples) would then count as render instead of tick; that's pure accounting.
  - Extend to arp-off MONO Sounds (needs care with voice handover order).

## Gotchas / commands

```sh
cd /home/user/wt/e3-spread-tick
git checkout perf/e3-spread-tick        && DBT_NO_SYNC=1 DBT_TOOLCHAIN_PATH=/home/user/DelugeFirmware ./dbt build release
git checkout perf/e3-spread-tick-probe  && DBT_NO_SYNC=1 DBT_TOOLCHAIN_PATH=/home/user/DelugeFirmware ./dbt build release
# runs: /home/user/perfwork/runs/.e3-queue.sh ELF:OUTNAME ...   (one emulator at a time, run_emu_rings.py, lean)
/home/user/perfwork/tools/e3_ticks.py runs/a runs/b    # quick per-render tick comparison (mine)
```

- The toolchain's clang-format isn't executable in place. I copied it into my scratchpad (`chmod +x`).
- The first runs used `run_emu.py` (ring only, ~10 s). `e3-v1-{N30,BU6}-lean1` are ring-only, so trust their
  summarize/probe numbers, not their ring statistics.
- N30 (no culls at 70%): step tick 17.0 -> 11.3 samples (ticks 32 -> 8/s; max 17.7 -> 11.9), songTickCost 17.8 -> 11.8.

---

# Final change: one queue for every note-on that can't start (branch `perf/pending-note-ons`)

User request (via the coordinator): make the E3 queue the **only** mechanism for note-ons that can't get a voice in
the current render, and remove the old pending machinery. The result is a PR for review on the user's fork.

| branch | commit | what |
|---|---|---|
| `perf/pending-note-ons` (off main 680b93c) | **031ef35** | "Start voices that can't start in this render from one queue". This is a729ab9 generalised. Builds clean |
| | **bc113ca** | "Remove the old pending-note machinery". Builds clean. Open for review as draft PR m-m-adams/DelugeFirmware#18 (any fix = a new commit on top) |
| `perf/pending-note-ons-probe` (throwaway) | 8d0284b | probe e339b27 cherry-picked on top of bc113ca. The conflicts in sound.cpp were resolved to the new code, with probe hooks moved into the queue (queued counts as a refusal, a start from the queue counts as a pending start, DZ_SCOPE slot 5 = one sent entry) |

ELFs:
- `/home/user/perfwork/elf/e3-final.elf`: plain bc113ca (md5 f43c8d92).
- `/home/user/perfwork/elf/pno2-probe.elf` (bc113ca + probe) and `pno2-{D_KIT20,BKIT20U4,BU6}.elf`: final measurement
  builds. `pno-*.elf` is the same code without the current-song check in the drain (see "Review fixes" below);
  the main measurement table was taken with it.
- Runner: `/home/user/perfwork/tools/run_emu_rings_pno.py`. It is run_emu_rings.py with plugin scope s5 =
  `Sound::startArpNoteOns`, because `process_postarp_notes` no longer exists.

## Design

One FIFO (`DeferredNoteOns`, `src/deluge/model/clip/deferred_note_ons.{h,cpp}`), 256 entries (~64 B each, 16 KB in
SDRAM BSS, no allocation). It has two entry kinds and one drain:

- **VOICE**: `DeferredNoteOns::startVoice(modelStack, sound, VoiceStart)`.
  - This is now the only way a Sound starts a voice for a new note. Sound::noteOn() (every source: sequenced,
    audition, MIDI in, kit-arp output via SoundDrum::noteOn) and Sound::startArpNoteOns() (the old
    process_postarp_notes: synth arp in render and tick, drum arps in Kit::doTickForwardForArp) both call it.
  - It starts the voice now if this render may (`!wouldRefuseVoiceStart()`) and the Sound has nothing queued.
    Otherwise it stores the arguments of noteOnPostArpeggiator(): Sound*, pre/post-arp note code, velocity, MPE
    values, sampleSyncLength, ticksLate (+ swung-tick stamp), samplesLate (+ time stamp), MIDI channel and
    invertReversed.
  - At drain time the ModelStack is rebuilt the way the Sound's renderer would build it:
    - SoundInstrument: activeClip + getParamManager(song);
    - SoundDrum: kit's activeClip, the drum's NoteRow and the row's paramManager, else the song's backed-up one;
    - sample preview: AudioEngine::paramManagerForSamplePreview (now `extern` in audio_engine.h).
- **SEQUENCED**: `tryDefer()` from InstrumentClip::processCurrentPos(). This is a729ab9's tick-cost saving: the note
  is held back before it reaches its Output, so the kit/instrument/arp bookkeeping of notes that can't start doesn't
  run in the tick.
  - Eligible notes: the Sound just plays the note. That means a SYNTH with polyphonic POLY or **AUTO** (review point
    b) and the clip arp off, or a KIT row with a SoundDrum, kit arp and drum arp off, and polyphonic POLY or AUTO.
  - MONO, LEGATO and CHOKE rows/synths are no longer "never held back" (review point a). They go through their
    Output at once and wait as VOICE entries if needed.
- **Drain**: `sendWhatCanStart()`, oldest first, while the render may start voices. It runs in routine_() right after
  the tick slot (counted as render time) and in the stem-export loop.
  - It doesn't depend on Sound::render, so render-skipping can't strand a note (review point 3).
  - A `sending` flag stops an entry being sent from re-queueing behind the same Sound's later entries.
- **Per-Sound order:** a new note-on for a Sound that still has queued entries always queues behind them (`mustWait()`
  scans the queue only when it isn't empty). So a mono/legato line can't be reordered by a later note grabbing the
  budget first.
- **Overflow:** a note starts immediately, over the budget. It's only a burst, and a note 256 renders late would be
  useless. Dropping it would be worse.

Forget hooks (review point 2). Every kind is dropped when its note ends before it got a voice:
- `Sound::noteOffPostArpeggiator()` -> `forgetNoteOff(sound, noteCodePostArp)` (VOICE). This covers note-offs from
  every source and arpeggiator gate-offs. `ALL_NOTES_OFF` forgets every entry of that Sound, both kinds.
- `NoteRow::stopCurrentlyPlayingNote()` -> `forgetNoteRow()` (SEQUENCED).
- `Sound::killAllVoices()`, `SoundDrum::choke()` (when it chokes), `~Sound` -> `forgetSound()` (both kinds).
- `~InstrumentClip` -> `forgetClip()`, `~NoteRow` -> `forgetNoteRow()`.
- `PlaybackHandler::endPlayback()` -> `forgetSequenced()`. VOICE entries from live notes survive a stop, as pending
  live notes did.

A queued VOICE note's arp note is marked PLAYING when it's handed over. So a note-off finds it, removes it from the
arp, and the hook drops the queued voice. No stale arp, MPE or render-skipping state is left behind.

Decisions:
- **Choke at queue time.** SoundDrum::noteOn() calls kit->choke() before Sound::noteOn(), as today, so the closed hat
  cuts the open hat when it is *triggered*. New: a choke also drops the choked drums' queued notes, so a waiting
  open hat can't start after the closed hat has cut it.
- **Mono/legato stealing and glide happen when the voice actually starts** (in noteOnPostArpeggiator), as they did for
  pending starts. Per-Sound FIFO keeps the order.
- **Mono note-priority fallback** (noteOffPostArpeggiator in MONO starting the previous held note) bypasses the
  budget. It retriggers the voice being released (reused, so no extra voice), and waiting would leave a gap in the
  line. Any queued entry for that note code is dropped first, so it can't start twice.
- **MIDI, CV and gate** don't use voices, so they never wait (unchanged).
- **ticksLate vs samplesLate:**
  - The queue passes ticksLate advanced by the swung ticks since the note was due. Old pending starts passed 0, which
    made a time-stretched sample's sync start (`sequenceSyncStartedAtTick = lastSwungTickActioned - ticksLate`) the
    tick on which the voice happened to start, not the tick the note was due on. The advanced ticksLate is right:
    sync is measured in song ticks from the note's due tick.
  - samplesLate (a late-started sample's skip-ahead, nonzero only for deliberate late starts) is passed on, plus the
    samples spent waiting.
  - Everything else starts at sample 0 of its render, as pending starts did.
- **MPE:**
  - VOICE entries capture the MPE values at queue time. A pending note used the ArpNote's values at start time; the
    ArpNote isn't updated by later expression, so that's the same values.
  - SEQUENCED entries read the NoteRow's MPE at send time, a few ms later. This is in the commit message.
- **NoteRow ABA** (review point c):
  - A SEQUENCED entry's NoteRow pointer is only compared with the clip's rows, never followed blindly.
  - `~NoteRow` drops entries for a deleted row, so a reused address can't carry a stale entry.
  - A row that moves in memory (rows inserted or deleted around it) is caught by the id/drum check: synth rows have
    unique y, kit rows a unique Drum.
  - So the remaining case is a deleted row whose address is reused by a new row with the same y/drum within the few
    ms, which is semantically the same row.
  - A per-row generation counter would add 4 bytes to every NoteRow and change its constructor, for no gain over this.
- **Order of a chord's waiting notes:** oldest first (tick order, clip by clip and row by row). Before, it was one per
  Sound per render in render order. This is in the commit message.

## What was removed (refs are to main 680b93c)

| what | where |
|---|---|
| `allowedToStartVoice()` checks and the refused/PENDING branch, `atLeastOneNoteLeftPending` and its `reassessRenderSkippingStatus` call (issue #2262) | sound.cpp 1581-1615 |
| `Sound::process_postarp_notes` (PENDING skip, budget check). Replaced by `startArpNoteOns`, which only hands each note to the queue | sound.cpp 2379-2406, sound.h 333; callers sound.cpp 2519, sound_instrument.cpp 453, kit.cpp 1255 |
| `Sound::render` arp-off `handlePendingNotes()` | sound.cpp 2498-2500 |
| render-skipping guard `!getArp()->hasPendingNotes()` + comment (issue #2262) | sound.cpp 2214-2219 |
| `ArpNoteStatus::PENDING`, `ArpNote::isPending()`, `isStartablePending()` | arpeggiator.h 151, 161-168 |
| `ArpeggiatorBase::handlePendingNotes()` / `hasPendingNotes()` (decl + def), `Arpeggiator::hasPendingNotes()` | arpeggiator.h 235-238, 376-377; arpeggiator.cpp 500-545, 1482-1494 |
| `ArpeggiatorBase::render()` stalling while a note was pending | arpeggiator.cpp 1499-1501 |
| `ArpeggiatorBase::doTickForward()` returning 0 (a tick every song tick) while a note was pending | arpeggiator.cpp 1573-1576 |
| PENDING writes: drum arp-off note-on, synth arp-off note-on, drum switchNoteOn, synth switchNoteOn + chord slots (now PLAYING for what's handed over) | arpeggiator.cpp 211, 390, 889, 1417, 1455 |
| sticky `anyPending = true` in Arpeggiator::noteOn and its comment | arpeggiator.cpp 270-273 |
| reset() comment about PENDING / E322 | arpeggiator.cpp 119-121 |
| kit arp's `noteStatus[0] == PENDING` condition, "held by the drum's own arp" comment | kit.cpp 1194, 1209 |
| "stays PENDING" comment | non_audio_instrument.cpp 224 |
| stem-export comment (the budget reset stays, issue #5010) | audio_engine.cpp 1401-1403 |

After this, `allowedToStartVoice()` and `wouldRefuseVoiceStart()` are called only from `deferred_note_ons.cpp`.

**Kept, renamed: the arp-off hand-off.** PENDING had a second job. While the arp plays a held note, the `notes`
element stays marked. If the arp is switched off while the note is held, `handlePendingNotes()` handed it back to be
played as a plain note (sticky `anyPending`). That's arpeggiator logic, not voice-budget logic, so it stays:
- The marker is now `ArpNoteStatus::ARPEGGIATED`.
- The hand-back is now `Arpeggiator::takeNoteHeldThroughArpOff()`, one per tick from `doTickForward()`, which returns
  0 only while handing notes back.
- `anyArpeggiated` is set in switchNoteOn.
- It covers the kit arp switched off with pads held, MIDI/CV arps, and a synth whose active clip changes arp setting.
  The arp menus already cut all notes for synths and drums.
- It no longer runs from Sound::render(), so for a synth it only happens while playing (ticks).

## Measurements (lean, Deluge speed, `sd-baseline.img`, run_emu_rings, 200 slices)

| song | run | culls/5 s | cull events hard / soft | voices | step tick mean / max (samples) | songTickCost | ticks/s | dStep1 queued / margin | chord latency | least queued / least time left |
|---|---|---|---|---|---|---|---|---|---|---|
| KIT20 | baseline (2) | 1.2 / 1.2 | 0/12 | 19.3 | 28.8/29.6 | 29.3 | 32 | 83.2 / 9.3 | 10.5 ms | 78 / 43 |
| | a729ab9 (2) | 0 / 0 | 0/0 | 19.4 | 15.2-15.8 | 15.5-16.3 | 8 | 96 / 40 | 10.4 ms | 92 / 54 |
| | **final** | **0** | 0/0 | 19.4 | **13.9/14.6** | **14.2** | **8** | **98.3 / 47.1** | 10.3 ms | 94 / 58 |
| BKIT20U4 | baseline (2) | 58.0 / 52.7 | 69/418, 43/471 | 18.0 | 26.7/27.4 | 27.2 | 32 | 78.0 / 7.1 | 13.2 ms | 72 / 8.8 |
| | a729ab9 (2) | 15.1 / 11.8 | 0/156, 0/120 | 18.7 | 14.7-16.3 | 15.0-16.7 | 8 | 88 / 17-22 | 12.7-12.9 ms | 82-84 / 16-18 |
| | **final** | **13.6** | **0/129** | 18.6 | **16.5/16.8** | 16.8 | **8** | 88.0 / 19.1 | 12.7 ms | 82 / 16.8 |
| BU6 | baseline (4) | 8.6 / 16.0 / 12.9 / 7.3 (mean 11.2) | 4-8 hard | 18.7 | 10.8-11.0 | 11.0-11.4 | 32 | 90 / 22 | 13.1 ms | 84 / 13 |
| | a729ab9 (4) | 6.1 / 7.0 / 6.2 / 5.7 (mean 6.3) | 0 hard | 18.8 | 7.6/8.0 | 7.9 | 8 | 93 / 32 | 13.2 ms | 86 / 17 |
| | **final (3)** | **6.3 / 7.0 / 6.2** (mean 6.5) | **0 hard** | 18.8 | 7.9/8.4 | 8.3-8.4 | 8 | 93.3 / 32.5 | 13.2 ms | 86 / 16.8 |
| BU8 | baseline (2) | 191.9 / 191.6 | 755/413 | 14.9 | 11.0/11.5 | 11.4 | 32 | 91.4 / 28.9 | 14.1 ms | 73 / 4.7 |
| | a729ab9 | 152.0 | 132/1352 | 16.0 | 7.5/8.0 | 7.9 | 8 | 91.4 / 28.4 | 14.6 ms | 84 / 10.9 |
| | **final** | **150.8** | **82/1300** | 16.1 | 7.8/8.4 | 8.1 | 8 | 90.4 / 25.5 | 14.6 ms | 83 / 9.9 |

Run dirs: `runs/pno-{KIT20,BKIT20U4,BU8}-lean1`, `runs/pno-BU6-lean{1,2,3}`. Load is unchanged (68/86/88/89%). Direness
is still 14 in every render.

The final change matches a729ab9 within noise on every benchmark. The kit step tick is 13.9 samples in KIT20, better
than a729ab9 because no voice start ever leaves a pending note, so no pending ticks at all. The BU6 step tick is 0.3
samples higher than a729ab9: that's the gate in Sound::noteOn (one queue check per note) for the one note per clip
that starts in the tick.

Confirmation with the exact PR code (`pno2-*`, bc113ca + probe; runs `runs/pno2-{KIT20,BKIT20U4,BU6}-lean1`):

| song | culls/5 s | hard/soft events | step tick mean/max | songTickCost | dStep1 queued / margin | chord latency | least queued / least time left |
|---|---|---|---|---|---|---|---|
| KIT20 | 0.0 | 0/0 | 15.8/16.3 | 16.3 | 95.7 / 38.8 | 10.4 ms | 91 / 54 |
| BKIT20U4 | 8.8 | 0/88 | 15.0/15.5 | 15.4 | 90.3 / 24.2 | 12.7 ms | 84 / 20.6 |
| BU6 | 6.9 | 0/71 | 7.9/8.4 | 8.3 | 93.1 / 32.1 | 13.1 ms | 86 / 16.8 |

The same as the `pno-*` runs within run-to-run spread. The kit step tick varies 14-16 samples between runs and builds,
as the baseline's did (24.9-28.8).

## Functional checks (emulator)

Test card `/home/user/perfwork/sd-e3.img` (content `/home/user/perfwork/sdcard-e3/SONGS`). Songs come from
gen_tickload.py, post-processed:

| song | what |
|---|---|
| E3ARP | 3 synth clips x 3-note chords, synth arp ON in every clip |
| E3KARP | kit of 8 synth rows, kit arp ON |
| E3DARP | kit of 8 synth rows, every drum's arp ON (sync 1/32) |
| E3POLY | 5 synth clips x 3 notes, polyphony mono / legato / auto / poly / poly |
| E3KPOLY | kit of 10 synth rows: choke x3, mono x2, legato, auto x2, poly x2 |
| E3AUD, E3AUDB | 1 clip x 8 rows open in clip view (E3AUDB: + 2 more clips, unison 6, gate 0.5, so load is high), for auditioning |

Probe runs (80 slices, lean, Deluge speed), baseline probe ELF vs final, `runs/pt-{base,pno,pno2}-<song>`:

| song | voice starts/s, baseline (tick + pending) | voice starts/s, final (tick + from queue) | voices, base / final | ticks/s base / final | culls | load |
|---|---|---|---|---|---|---|
| E3ARP (synth arp) | 48.0 (0 + 48.0) | 48.1 (48.1 + 0) | 1.5 / 1.5 | 16 / 16 | 0 / 0 | 22% |
| E3KARP (kit arp) | 16.0 | 16.0 | 1.0 / 1.0 | 16 / 16 | 0 / 0 | 19% |
| E3DARP (drum arps) | 128.1 (0 + 128.1) | 128.0 (64 + 64; 4 queued at most) | 3.8 / 3.7 | 16 / 16 | 0 / 0 | 36-37% |
| E3POLY (mono/legato/auto) | 120.5 (32.1 + 88.4) | 120.5 (32.1 + 88.4) | 11.1 / 11.0 | 16 / **8** | 0 / 0 | 46% |
| E3KPOLY (choke/mono/legato/auto rows) | 80.0 (32 + 48) | 80.2 (32.1 + 48.1) | 8.0 / 8.0 | 16 / **8** | 0 / 0 | 44-45% |

Notes on the table:
- Every arp keeps stepping at the same rate, and every polyphony mode starts the same number of voices per second
  with the same voice count.
- Synth-arp notes now start in the tick or render where the arp produces them; before, every one was counted as a
  "pending" start because it went through process_postarp_notes.
- With 8 drum arps stepping on the same tick, 4 start and 4 wait for the next render. That's the budget at this load
  (direness <= 12, so 4 per render), the same as before.
- The pending ticks are gone in the mono/legato/choke songs too: E3POLY and E3KPOLY run 8 ticks/s instead of 16.
- `pno2` (final) and `pno` agree to 0.1/s on E3KPOLY and E3DARP.

Audition / live notes (`/home/user/perfwork/song/e3_audition_check.py`):
- What it does: host-paced emulator, WAV capture through QEMU's wav audiodev (no host audio), assert_no_aborts, probe
  counters dumped between phases. Runs: `runs/aud-*`, `runs/audb2-*`.
- Stopped, holding 8 audition pads (E3AUD): 8 voices start (final and baseline alike). The WAV goes from silence to
  -20 dB while held and back to silence on release, so note-ons and note-offs reach the voices.
- Playing the heavy song (E3AUDB, direness 14) with 5 rounds of 4-8 audition pads held:
  - Final: 21 live starts at once and 2 queued (refusedOther 2), which then started from the queue.
  - Baseline: 5 live starts.
  - The counts depend on host timing (whether a row's sequenced note is sounding when its pad goes down).
- No data or prefetch aborts in any audition run.
- MIDI input wasn't tested in the emulator. It enters through the same MelodicInstrument/Kit -> Sound::noteOn() path
  as auditioning.

Unit tests: none cover the arpeggiator, Sound or the queue. `tests/unit` only uses `arpeggiator_rhythms.h`, which is
unchanged, so I didn't run them.

## Review fixes vs a729ab9 (all in 031ef35)

- (a) a729ab9's kit eligibility excluded only CHOKE while its comments said mono/legato were excluded. Now there's one
  rule for both synths and kits: POLY or AUTO, arps off. Everything else waits as a voice start, so nothing bypasses
  the budget.
- (b) Synths with AUTO polyphony are held back too.
- (c) NoteRow ABA: `~NoteRow` drops its entries; moved rows are caught by the y/Drum check (see Design).
- Added after the first measurement (no effect on the numbers): a queued voice start's Sound must still be part of the
  current song (an output in `currentSong->firstOutput`, and for a drum, in its kit's drum list). The audio routine can
  run (routineWithClusterLoading) while a song is being torn down, and the drain must not rebuild a ModelStack from a
  half-deleted kit.

No fixes were needed after bc113ca: no new commits.

## Risks / what to look at in review

1. **The arp-off hand-off** (`ArpNoteStatus::ARPEGGIATED`, `takeNoteHeldThroughArpOff()`) is the one place the old
   PENDING semantics live on. Check that keeping it, and that it now only runs from ticks, is wanted. Dropping it
   entirely would remove ~25 more lines but changes what happens when the kit arp is toggled with pads held.
2. **ModelStack rebuilt at drain time** (`setupModelStackForSound()`): SoundInstrument -> activeClip/getParamManager;
   SoundDrum -> kit activeClip + row paramManager or the song's backed-up one; preview sound. If some note source used a
   different ParamManager (e.g. an arranger clip that isn't the active clip), a queued note now starts with the active
   one.
3. **Per-Sound FIFO** (`mustWait()` scans the queue when it isn't empty) and the `sending` flag that stops re-queueing
   during the drain.
4. **Choke drops queued notes** of the choked drums (new). **The mono fallback in noteOffPostArpeggiator bypasses the
   budget** (argued above).
5. **Overflow starts immediately** (256 entries).
6. Behaviour notes for the PR: chord notes start oldest first rather than one per Sound per render; held-back
   sequenced notes read MPE at send time; time-stretch sync uses the due tick; a synth's held notes come back after an
   arp-off only while playing.

## Draft PR description

```
## Summary

Only a few voices may start per render (AudioEngine::allowedToStartVoice(): 4, or 1 when the CPU is busy), so a big
chord starts over several renders. Until now each note that couldn't start was left PENDING in its Sound's
arpeggiator and found again later by Sound::render() (one per Sound per render), by an arpeggiator tick, or not at all
if the Sound was render-skipping (#2262). Every note-on still went through its instrument, kit arp and arpeggiator
first, so the song tick paid the full note-on bookkeeping for the 19 of 20 notes of a step that couldn't start, on top
of the render it precedes. Pending notes also made ArpeggiatorBase::doTickForward() ask for a tick on every song tick,
so 3-4 extra ticks per step cut renders short.

This replaces all of that with one queue, DeferredNoteOns:

- Every voice start goes through DeferredNoteOns::startVoice(): start now if this render may, otherwise queue. That
  covers sequenced notes, auditioning, MIDI input, and notes from the synth, kit and drum arpeggiators.
- The song tick holds back a sequenced note that couldn't start anyway before it reaches its Output, when the Sound
  just plays it (poly/auto, arps off, not choke), so the tick doesn't pay for its bookkeeping.
- Once per render, after the tick and before the song renders, sendWhatCanStart() starts what that render may, oldest
  first. That's the render that would have started the note from pending before, so voices start no later.
- A note never starts before an earlier one for the same Sound (mono/legato order). A queued note is dropped by a
  note-off for it, all-notes-off, its NoteRow stopping, a choke, or its Sound/Clip/NoteRow going away.

The second commit removes the old machinery: ArpNoteStatus::PENDING, handlePendingNotes()/hasPendingNotes(), the
pending lookups in Sound::render(), ArpeggiatorBase::render() and doTickForward(), and the render-skipping guard for
pending notes. The arp's hand-back of held notes when it's switched off is kept, as ArpNoteStatus::ARPEGGIATED /
Arpeggiator::takeNoteHeldThroughArpOff().

Measured in delugemu at Deluge speed (20 notes per 16th step):

| song | culls/5 s before -> after | step tick (samples) | songTickCost |
|---|---|---|---|
| kit, 20 synth rows | 1.2 -> 0 | 28.8 -> 14-16 | 29 -> 14-16 |
| same, unison 4 | 55 -> 9-14 (hard: 56 -> 0) | 26.7 -> 15-16.5 | 27 -> 15-17 |
| 5 synths x 4-note chords, unison 6 | 11.2 -> 6.5 (avg of 3-4 runs) | 11.0 -> 7.9 | 11.2 -> 8.3 |
| same, unison 8 (overloaded) | 192 -> 151 (hard: 755 -> 82) | 11.0 -> 7.8 | 11.4 -> 8.1 |

Ticks per second drop from 32 to 8 (one per step). Load, voice counts and chord start latency are unchanged.

## Type of change

- [x] Refactor (no behavior change)

Small behaviour differences for notes that can't start in their own render:
- a chord's waiting notes start oldest first (clip by clip, row by row) instead of one per Sound per render;
- a held-back sequenced note reads its row's MPE a few ms later;
- time-stretched samples sync to the tick the note was due on;
- a choke also drops the choked drums' waiting notes;
- a synth's held notes come back after its arp is switched off only while playing.

## What to test

Busy songs that make the CPU indicator blink. In each, check notes still start, chords complete, and nothing hangs:
- big chords across several synths;
- a 20-row kit;
- synth, kit and drum arps on;
- mono, legato and choke rows;
- auditioning and MIDI input while playing.

Also try editing while playing (delete or move rows, change kit drums, swap songs, stop and start, record into the
arranger), stem export, and the sample browser preview.

## Checklist

- [ ] I tested this on hardware (or it cannot affect firmware behavior)
- [x] It works on both OLED and 7SEG
- [ ] User-visible changes have an entry in `docs/community_features.md`
- [ ] Behavior changes are behind a runtime setting (or this is not a behavior change)
```

