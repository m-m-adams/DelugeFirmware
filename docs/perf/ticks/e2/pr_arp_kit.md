## Summary

Two small changes that take most of the per-note bookkeeping out of the song tick for kits (and some of it for synths), without changing what plays.

**1. arp: rebuild `notesByPattern` only when the PATTERN note mode reads it**

`Arpeggiator::rearrangePatterntArpNotes()` rebuilt the pattern-ordered note list (one `ResizeableArray::insertAtIndex` per held note) on every note-on and note-off, whatever the arp mode, including with the arp off. Only `switchNoteOn()` in `ArpNoteMode::PATTERN` reads that list. Every kit note goes through the kit arpeggiator, so a kit playing 20 rows on a step did 20 inserts per note-on and 19 per note-off: O(rows²) per step, about 2k instructions per note each way.

Now `rearrangePatterntArpNotes()` copies the settings' 16-byte `notePattern` and marks the list stale, and `switchNoteOn()` rebuilds it from that copy right before reading it. The `notes` array only changes where the rebuild was requested (noteOn insert, noteOff delete, `removeDrumIndex`, `reset`), and the pattern is copied at those same points. So whenever the list is read, it holds exactly what the eager rebuild would have left there, even if the pattern was regenerated or the settings object changed in between.

**2. kit: take the drum's note row from the model stack instead of searching for it**

`Kit::noteOnPreKitArp()` / `noteOffPreKitArp()` called `getNoteRowForDrum()` (a linear search over all rows) for every note. The sequencer (`NoteRow::processCurrentPos`, `InstrumentClip::sendPendingNoteOn`, including the deferred note-ons) and auditioning already pass that NoteRow and its index in the model stack. The new `Kit::getNoteRowForDrumInActiveClip()` uses them after checking all of these:
- the model stack's clip is the kit's active clip;
- the index is in range;
- the row at that index *is* the model stack's row;
- that row holds this drum.

Otherwise it falls back to the search. A drum has at most one NoteRow per clip, so this is the row (and index) the search returns.

### Measurements

Emulator (delugemu) at Deluge speed (`timectl=1,cachesim=0,cpi=1.45`), lean mode, 200 slices, 25 s skipped. Test songs start 20 voices on every 16th step (kits: 20 synth rows; BU6: 5 synths × 4-note chords, unison 6). Before = PR #18's 09d4d3a + probe, whose firmware is identical to main 3755636a2 on every path these songs exercise. main only adds 0f164f507 (cross-screen note-edit UI) and 601ee7ca8 (USB audio volume). After = this PR on main + the same probe. Measured on main **without #19** (E1's culling/direness change). Kit culls are 3 runs before and 2 after; BU6 is 3 runs before and 2 after.

| song | step tick, samples (before → after) | culls / 5 s (hard) | least queued | least time left | renders past empty | chord smear mean | voices |
|---|---|---|---|---|---|---|---|
| KIT20 (kit, 20 rows) | 17.9 → **10.8** | 0 → 0 | 89 → 96 | 46.4 → 55.7 | 0 → 0 | 4.7 → 4.4 ms | 20.0 → 20.0 |
| BKIT20U4 (kit, 20 rows, unison 4) | 16.3 → **9.3** | 21.8 / 18.7 / 18.4 (0 hard) → **0.0 / 0.0** | 81-82 → 87 | 14.3-16.5 → 27.4 | 0 → 0 | 7.1 → 6.8 ms | 19.3 → 19.7 |
| BU6 (5 synths × 4 notes, unison 6) | 8.4 → 7.7 | 10.4 / 10.7 / 9.8 (0.3-0.5 hard) → 6.9 / 7.6 (0-0.1 hard) | 81 → 81 | 15.9 → 16.4-16.9 | 0 → 0 | 7.2 → 7.2 ms | 19.4 → 19.4 |

Load is unchanged (69% / 87% / 88%).

Per-call instruction counts on BKIT20U4 (full mode, exact), measured together with the follow-up voice-start PR:
- song tick per step: 90.5k → 52.3k;
- `NoteRow::processCurrentPos`, which is where the kit note-offs happen: 3,612 → 1,760.

Measured on the pre-#18 code (main 680b93c), the kit-level overhead per note went from about 3.7k / 3.1k (on/off) to about 0.7k / 0.6k instructions.

### Behaviour check: note-by-note comparison

The test builds log every voice start (Sound, note, velocity) and every note-off. Main + log and this PR (stacked with the voice-start PR) + log played these songs for about 30 s each:
- synth arp UP;
- synth arp PATTERN with a fixed notePattern;
- kit arp PATTERN over 20 rows;
- synth arps, kit arp, drum arps;
- mono / legato / auto / poly synths;
- choke / mono / legato / auto / poly kit rows.

In every one of these songs, each Sound's sequence of starts and note-offs is identical, and so is the global order of all starts and of all note-offs.

In the two heavily loaded chord songs (BKIT20U4, BU6):
- the same notes start and stop in every step (256/256 and 264/264 steps identical);
- note-offs come in the identical order;
- only the order of starts *within* a step differs. That order comes from the voice-start time budget, which depends on how long the tick and each start take. Starts moved by -300..+208 samples, slightly earlier on average.

Songs whose arp pattern is generated randomly at load differ between any two builds (RNG state), so the PATTERN tests use a fixed `notePattern`.

## Type of change

- [ ] Bug fix
- [ ] New feature or changed behavior
- [x] Refactor (no behavior change)
- [ ] Chore (build, CI, docs, tooling)

## What to test

- Kits playing many rows per step: the same sound, a lighter CPU load in the tick.
- **Arpeggiator in PATTERN note mode** (synth, kit arp, MIDI/CV): hold notes, check the pattern order. Then regenerate the pattern (note mode menu) while notes are held: the order should follow the same rules as before (the new pattern takes effect at the next note-on/off).
- Kit arp on/off with pads held, removing a drum row while the kit arp is running (`removeDrumIndex`), auditioning kit rows, MIDI into kit rows (that path takes the fallback search).

Untested:
- hardware;
- MIDI input in the emulator;
- MIDI/CV arps in PATTERN mode;
- deleting a kit row while its notes play.

What to look at in review:
- Every place that changes `Arpeggiator::notes` must still call `rearrangePatterntArpNotes()`.
- `getNoteRowForDrumInActiveClip()` relies on "one NoteRow per drum per clip".
- Two edge differences in commit 1, neither reachable in normal use:
  - A null `settings` (a MIDI/CV instrument with no active clip) used to read the pattern through a null pointer when notes were held. It now keeps the previous copy.
  - After an allocation failure inside `noteOn()`, the lazy rebuild may include a half-inserted note that the eager code left out.

Notes, scripts and all run data: [`docs/perf/ticks/e2-cheap-note-on.md` on perf/ticks/claude-e2-on-main](https://github.com/m-m-adams/DelugeFirmware/blob/perf/ticks/claude-e2-on-main/docs/perf/ticks/e2-cheap-note-on.md).

## Checklist

- [ ] I tested this on hardware (or it cannot affect firmware behavior)
- [ ] It works on both OLED and 7SEG
- [ ] User-visible changes have an entry in `docs/community_features.md`
- [ ] Behavior changes are behind a runtime setting (or this is not a behavior change)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_011tnhfEs1tfKLppNdGBRRup
