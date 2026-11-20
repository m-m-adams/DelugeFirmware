# Phase 1C: TICKLOAD test songs

Generator: `/home/user/perfwork/song/gen_tickload.py` (Python 3, stdlib only).
Output: `/home/user/perfwork/sdcard/SONGS/TICKLOAD*.XML`, all songs directly in `SONGS/` (6 base songs + 4 FX songs).
README section for the delugemu PR (`tools/perf/gen_tickload.py`): `/home/user/perfwork/song/README.md`.
Emulator check script: `/home/user/perfwork/song/emu_check.py`.

## What the song does

The song tests the hypothesis that drops happen when the song ticks forward. On every 16th-note step, N voices
start at the same tick. Defaults:

| | |
|---|---|
| tempo / length | 120 BPM, 1-bar loop (384 ticks), 16 steps, no swing, no arp, no automation |
| voices per step | 20 = 5 synth clips x 4-note chords (one SoundInstrument per clip) |
| note length | gate 1.0 = 24 ticks, back to back. This is the Deluge's default 16th note, so step k's note-offs and step k+1's note-ons land in the **same** tick |
| amp env | attack 0, decay 20, sustain 50, **release 0**. Release 0 is far above `Voice::hasReleaseStage()`'s 18359 threshold, so a note-off goes straight to `FAST_RELEASE` and the old voice is gone within about one render window. Steady state is about N voices, and the spike is the starts, not a growing voice count |
| sound | subtractive, osc1 saw, osc2 square (-12, level from `setupAsDefaultSynth`), 24 dB ladder LPF (freq/res/cables as the default synth), unison 1, per-Sound `maxVoices = max(8, 2*chord)` so the voice limit never steals. No samples, so nothing streams from SD |
| FX | off: no reverb or delay sends, modFX none. `--fx` adds reverb send 15 and delay feedback 15 (16th sync) on every sound |
| volume | per-sound volume param 0x30000000 (the default synth uses 0x50000000), so 20 voices don't slam the limiter |

Standard set (`gen_tickload.py` with no arguments):

| file | content |
|---|---|
| `SONGS/TICKLOAD.XML` | default, 5 synth clips x 4 notes = 20 starts/step |
| `SONGS/TICKLOAD_KIT20.XML` | 1 kit with 20 synth-type drum rows (no samples), 1 note/row/step = 20 starts/step. Each row goes through its own NoteRow processing and its own Sound |
| `SONGS/TICKLOAD_N10.XML` | 5 x 2 = 10 starts/step |
| `SONGS/TICKLOAD_N30.XML` | 5 x 6 = 30 starts/step (maxVoices 12) |
| `SONGS/TICKLOAD_FX.XML` | default + reverb/delay sends |
| `SONGS/TICKLOAD_U4.XML` | default with unison 4 (the firmware's default-synth unison). The render is about 4x heavier per voice, so use it if 20 plain voices don't load the CPU enough to cull |

Kit drum pitches are spread with per-drum `transpose` (note 60 + transpose). Synth chords stack thirds from base note
36 + 7c mod 24. Pitch makes no difference to CPU cost here.

## Usage

```sh
cd /home/user/perfwork/song
./gen_tickload.py                          # standard set (idempotent re-run)
./gen_tickload.py -v                       # same + per-tick table of note starts/ends
./gen_tickload.py -o X.XML --clips 4 --chord 6 --bpm 140 --fx      # custom song
./gen_tickload.py -o X.XML --voices 23 --clips 4                    # 23 voices split 6/6/6/5
./gen_tickload.py -o X.XML --kit 32                                 # kit with 32 synth drum rows
./gen_tickload.py --stage KIT20 /home/user/perfwork/sdcard_kit20     # card content holding ONLY that variant
/home/user/perfwork/bin/mksd /home/user/perfwork/sdcard_kit20 /home/user/perfwork/sd_kit20.img
./gen_tickload.py --check FILE.XML ...     # static validation + per-tick table of existing songs
```

FX knobs: `--fx-set SPEC`, `--fx-all`, `--fx-vary`, `--song-fx SPEC`, `--kit-fx SPEC`, `--only`, `--force`
(see "Effects" below). Other knobs: `--gate` (fraction of a step; <1 moves the note-offs to their own tick), `--step-16ths` (start notes
every n 16ths), `--bars`, `--velocity`, `--osc1/--osc2` (osc type; `--osc2 off` mutes osc B), `--unison`,
`--release` (UI 0..50), `--volume`, `--max-voices`, `--fx-reverb/--fx-delay`.

## Effects (added 2026-11-20)

`--fx` (reverb + delay sends) is unchanged. With no new options the output is **byte-identical** to before: I
checked all six standard songs and two custom configs (`--voices 23 --clips 4 --gate 0.5 --bpm 140 --unison 3`;
`--kit 7 --fx --osc2 off`) against a snapshot of the previous generator.

The generator never rewrites a song in place. An existing identical file is left untouched ("unchanged"). A
different one is refused unless `--force` is given. It never deletes anything.

New options. Each SPEC is a comma list of `UNIT[=VALUE]`, and `all` means every unit except `route`:
- `--fx-set SPEC` / `--fx-all`: effects on every sound (synth instruments and kit rows).
- `--fx-vary`: varies the effects per sound (synth clip or kit row):
  - modFX type cycles through all 7 types in enum order;
  - LPF modes cycle through 5, HPF modes through 3;
  - each sound gets one of the selected distortion units (bitcrush, srr, clip, fold) in turn.
- `--song-fx SPEC`: master effects. `--kit-fx SPEC`: kit-level (affect-entire) effects, kit layout only.
- `--only A,B`: write only some standard variants. `--force`: overwrite a changed song.

| unit | sound | kit | song | firmware tag/attr (where it's read/written) | what turns it on in the render |
|---|---|---|---|---|---|
| modfx[=type] | ✓ | ✓ | ✓ | `modFXType` attr. Strings from util/functions.cpp `fxTypeToString`: flanger, chorus, phaser, StereoChorus, TapeWarble, dimension, grainFX. Params: sound `modFXRate`/`modFXDepth` (patched) and `modFXOffset`/`modFXFeedback` (unpatched); song/kit params with the same names (GlobalEffectable::writeParamAttributesToFile) | Sound: `Sound::setModFXType` allocates the modfx buffer, or a GranularProcessor + a 512 KiB stealable grain buffer, at load. Song/kit: `processFXForGlobalEffectable` allocates at render time if `getActiveModFXType` passes, i.e. the param named by `modFXCurrentParam` is above minimum. The default "feedback" sits at minimum, so the generator writes `modFXCurrentParam="depth"` and depth = 30 |
| delay[=sync] | ✓ | ✓ | ✓ | `<delay pingPong analog syncLevel syncType>` (ModControllableAudio::writeTagsToFile); sound `delayFeedback`/`delayRate`, song/kit `<delay rate feedback>` in the params | feedback/amount above minimum (`Delay::isActive` once the buffer is allocated). Sync: file = internal - 1 + mag (Song::convertSyncLevel…), and level L lasts `3 << (9-L)` ticks. At mag 2, 16th = file 7 (the template value), 8th = 6, 4th = 5, … syncType is the raw enum: 0 even, 10 triplet, 19 dotted (model/sync.h) |
| analog | ✓ | ✓ | ✓ | `<delay analog="1">` | analog delay model |
| reverb | ✓ | ✓ | ✓ | sound `reverbAmount` (patched), song/kit `reverbAmount` (unpatched send); song `<reverb model>` (dsp/reverb/reverb.hpp: 0 freeverb, 1 mutable, 2 digital) | send above minimum feeds the global reverb. `--song-fx reverb=digital` picks the model and sends the whole mix |
| bitcrush / srr | ✓ | ✓ | ✓ | `bitCrush`, `sampleRateReduction` | `processSRRAndBitcrushing` when above minimum; on the song this runs in `renderSongFX` |
| clip | ✓ | ✓ | – | `clippingAmount` attr (ModControllableAudio::writeAttributesToFile), UI 0..15 (gui/menu_item/fx/clipping.h) | > 0. `renderSongFX` doesn't saturate, so song level refuses it |
| fold | ✓ | – | – | `waveFold` (LOCAL_FOLD) | above minimum, per voice |
| eq | ✓ | ✓ | ✓ | `<equalizer bass treble bassFrequency trebleFrequency>`; bass/treble use `getParamFromUserValue` special case v·42949672 | bass/treble != 0 (`hasBassAdjusted` / `hasTrebleAdjusted`) |
| lpf / hpf | ✓ | ✓ | ✓ | `lpfMode`/`hpfMode` strings from filter_config.cpp `filterMap` (12dB, 24dB, 24dBDrive, SVF_Band, SVF_Notch / SVF_Band, SVF_Notch, HPLadder); sound `hpfFrequency`; song/kit `<lpf frequency>`/`<hpf frequency>` | HPF on when its frequency is above minimum (set to 10). Song/kit LPF cutoff set to 35 (on when below max) |
| route | ✓ | ✓ | ✓ | `filterRoute`: H2L, L2H, PARA (`routeMap`) | – |
| comp | ✓ | ✓ | ✓ | sound/kit `compressorThreshold` param + `<audioCompressor thresh ratio …>`; song master `<audioCompressor thresh>` | sound (sound.cpp render) and kit (GlobalEffectableForClip) run the compressor when the threshold param > 0, i.e. UI > 25. The song master compressor always runs; `thresh` is a knob position (dsp/compressor/rms_feedback.h: 0 = threshold 1.0 = none) |
| sidechain | ✓ | ✓ | – | sound `sideChainSend` attr (Sound::writeToFile; 2147483647 as SampleBrowser gives a kick); cable `compressor` (= PatchSource::SIDECHAIN, sourceToString) → `volumePostReverbSend` ("volume ducking" in gui/ui/menus.cpp); kit `sidechainCompressorVolume` | the sender calls `AudioEngine::registerSideChainHit` on every note-on, which is extra tick-time work. Receivers render the sidechain envelope (`isSourcePatchedToSomething(SIDECHAIN)`) |

Not offered: stutter. `<stutter quantized reverse pingPong>` is configuration only; stutter runs while the knob is
held, so a song file can't switch it on.

New variants (standard set; the other ten files in `SONGS/` were not touched):

| file | content | static check: active FX units |
|---|---|---|
| `TICKLOAD_FXALL.XML` | default 5×4 + `--fx-all` (chorus, analog 16th delay, reverb 20, bitcrush 30, srr 20, clip 5, fold 20, EQ +15, 24dBDrive LPF, HPLadder HPF, comp 35, sidechain from SYN1) + `--song-fx all` | 5 sounds × 12 units + 10 master units |
| `TICKLOAD_FXVARY.XML` | 7 clips (3/3/3/3/3/3/2 = 20 voices), `--fx-all --fx-vary --song-fx all` | one modFX of each of the 7 types; distortions bitcrush/srr/clip/fold/…; LPF modes 12dB→SVF_Notch; HPF modes cycle |
| `TICKLOAD_GRAIN.XML` | default + `--fx-set modfx=grain,reverb --song-fx modfx=grain` | 5 grainFX sounds + master grain |
| `TICKLOAD_KITCHAIN.XML` | kit of 20 synth rows, `--fx-all --fx-vary --kit-fx all --song-fx all` | 20 rows with varied chains (all 7 modFX types), kit-level 11 units, master 10 units |

Each suffix (FXALL, FXVARY, GRAIN, KITCHAIN) is unique as a dzSongPath substring. `check_variant_names()` asserts
this for the standard set, and `warn_dir_name_clashes()` checks the actual directory.

**Name clash, not mine:** another agent (experiment E4, its own generator copy
`/home/user/perfwork/song/gen_tickload_e4.py`, left untouched) added `SONGS/TICKLOAD_E4FX.XML`. dzSongPath "FX" now selects
`TICKLOAD_E4FX.XML` (strcasecmp: "e4fx" < "fx."), not `TICKLOAD_FX.XML`; the generator prints a warning about it.
Workaround: patch dzSongPath to "D_FX" ("loaD_FX" matches TICKLOAD_FX, TICKLOAD_FXALL and TICKLOAD_FXVARY but not
E4FX, and '.' < 'a' picks TICKLOAD_FX.XML). Alternatively, rename one of the two files.

### FX validation (2026-11-20)

- **Stock firmware, emulator** (`emu_check.py`, card `/home/user/perfwork/sd-fx.img`, built with `mksd` from
  `/home/user/perfwork/sdcard`; `sd.img` was left alone; one emulator at a time). All four new songs load and play:
  - the OLED shows the song name and 120 BPM, with no error popup (`Sound::setModFXType` would show a RAM error);
  - PLAY LED on, song view shows 5 / 7 / 5 / 1 clip rows playing;
  - 0 aborts in the `-d int` log.
- **FX really run: probe firmware at Deluge speed.** `probe-base.elf` copies patched with `set_song.py`, card
  `sd-fx.img`, `run_emu.py --plugin timectl=1,cachesim=0,cpi=1.45 --lean --slices 60`, `summarize.py --skip 5`.
  Runs are in the scratchpad (not kept).

| song | load % | culls / 5 s | avg voices | render cycles / render (no-tick renders) | ticks actioned / s |
|---|---|---|---|---|---|
| TICKLOAD (baseline) | 60 | 0 | 19.6 | 288 k | 23.5 |
| FXALL | 70 | 868 | 3.7 | 397 k | 40.1 |
| FXVARY | 77 | 900 | 3.6 | 515 k | 40.1 |
| GRAIN | 83 | 0 | 19.2 | 566 k | 32.0 |
| KITCHAIN | **145** (overloaded: 44.5k frames/s, the budget is gone even at 1.5 voices) | 809 | 1.5 | 1.98 M | 51.0 |

  The FX are clearly active: render cost per render goes up 1.4x to 7x, and FXALL/FXVARY cull down to about 4
  voices. KITCHAIN is a stress-everything song (22 analog delays, 22 modFX incl. 2 grains, 20 compressors). The FX
  alone exceed the budget, so it's useful for allocation and robustness tests, not for tick-vs-render measurement.
  For that, FXALL/FXVARY (FX costs leave little headroom, so culls happen) or GRAIN (83 %, no culls) are better.
  Lighter chains can be built with `--fx-set`.
- **Observation for the tick investigation:** "ticks actioned per second" rises from 23.5 to 32–51 with FX. Tempo
  and notes are unchanged, so synced FX (synced delay, sidechain, grain tempo) appear to schedule extra timer ticks
  (sync events on top of the 16 note steps). I didn't dig into which code path does it.
- A WAV capture of the effect tails after STOP was not conclusive. QEMU's wav audiodev follows emulated output
  timing, and the stop point couldn't be placed reliably, so I used the probe load numbers instead.
  `wav_tail.py` is kept in `/home/user/perfwork/song/` but is not needed.

## Which song the probe autoplays (ordering)

**FAT directory order is unpredictable.** FatFS `f_readdir` returns entries in on-disk order. delugemu's
`scripts/mksd.sh` runs `mcopy -s CONTENT/* ::/`, and mtools copies a directory's files in host `readdir` order. On
ext4 that is htree hash order: neither creation order nor name order. I checked this: `mdir` of a card built from
the six files listed `TICKLOAD_N30.XML` first and `TICKLOAD.XML` fourth. So "first by FAT order" would pick a
random variant, and no file naming fixes that.

**What the probe actually does.** Phase 1B, `/home/user/wt/probe` `src/deluge/io/debug/dz_probe.cpp`: it scans
`/SONGS` (not subfolders, skipping `AM_DIR`) for `*.XML` whose name contains `dzSongPath` (case-insensitive,
default "TICKLOAD"). It keeps the **strcasecmp-smallest** name, so directory order doesn't matter.
`'.'` (0x2E) sorts before `'_'` (0x5F), so `TICKLOAD.XML` wins for "TICKLOAD". Every variant suffix is unique, so
`/home/user/perfwork/tools/set_song.py <elf copy> KIT20` (or N10, N30, FX, U4) selects exactly that variant on
the same card. So all songs go directly in `SONGS/`. An earlier version of the generator put the variants in
`SONGS/VARIANTS/`; re-running the generator removes that folder.

Alternative for any other matching rule: `gen_tickload.py --stage KIT20 DIR` writes a card content dir whose `SONGS/`
holds only that variant. Then run `/home/user/perfwork/bin/mksd DIR IMG`.

## XML structure and where each piece comes from

The tag and attribute sets follow what current firmware writes. They are cross-checked against
`/home/user/DelugeFirmware/contrib/audio_export/multi_sample/Multisample_Template_Full_Bar.XML`, a real
`c1.3.0` song with a kit of 128 synth-type drums. It is the only song XML in either repo; delugemu has no song files.

- **Root `<song>`** (`Song::writeToFile` / `readFromFile`, model/song/song.cpp).
  `firmwareVersion="c1.3.0"` and `earliestCompatibleFirmware="4.1.0-alpha"` are as in the template. A higher
  earliest version would make `tryReadingFirmwareTagFromFile` refuse the file. No `preview`; it's optional.
  `yScrollSongView = max(-7, nclips-8)`, which puts the clips in the top rows of song view.
  Children: `modeNotes`, `reverb`(+`compressor`), song `delay/sidechain/audioCompressor/stutter`, `songParams`,
  `instruments`, `sections`, `sessionClips`.
- **Timing.** `inputTickMagnitude="2"` gives `insideWorldTickMagnitude` = 2 (= `FlashStorage::defaultMagnitude`).
  `Song::getBarLength()` = `increaseMagnitude(96, 2)` = **384 ticks/bar**, and `getSixteenthNoteLength()` = **24**.
  The template's one-bar clip has `length="384"`.
  Tempo: `Song::setBPMInner` computes samples per tick = 110250 / (BPM x 2^mag) = 229.6875 at 120 BPM. This is
  stored as 32.32 fixed point: `timePerTimerTick="229"`, `timerTickFraction="-1342177280"` (low word as signed
  int32), identical to the template's 120 BPM. `songParams tempo` = BPM x 100 (0x2EE0). On load the firmware calls
  `setBPM(calculateBPM())` from the tick time anyway.
  `swingAmount="0"`, `swingInterval="7"` (the file value for 16ths at magnitude 2:
  internal = file + 1 - mag = 6 = `8 - defaultMagnitude`).
- **Synth instruments.** `<sound presetName=... presetFolder="SYNTHS">` in `<instruments>`. The `sound` tag makes a
  `SoundInstrument` (Song::readFromFile). `presetName` is `Instrument::getNameXMLTag()` and is read by
  `Output::readTagFromFile`. Attribute order follows `SoundInstrument::writeDataToFile`, then
  `Instrument::writeDataToFile`, `Output::writeDataToFile` (isArmedForRecording, activeModFunction), then
  `Sound::writeToFile` (polyphonic, voicePriority, mode, ModControllableAudio attrs, maxVoices). Then `osc1/osc2`
  (`writeSourceToFile`: type, transpose, cents, retrigPhase), `lfo1..4`, `unison`, `modKnobs`, and the
  ModControllableAudio tags. There's no `<defaultParams>`: in a song, an instrument with a clip keeps its params in
  the clip's `<soundParams>`, and the firmware also omits them when `activeClip` is set.
- **Clip to instrument matching.** `InstrumentClip::readFromFile` stores `instrumentPresetName` /
  `instrumentPresetFolder`, and the output type comes from which params tag it sees: `<soundParams>` sets SYNTH,
  `<kitParams>` sets KIT. So every synth clip **must** carry `soundParams` and every kit clip `kitParams`.
  `InstrumentClip::claimOutput` then calls `song->getInstrumentFromPresetSlot(type, …, name, dirPath)`. A miss
  returns `FILE_CORRUPTED`, so names are unique ("TICKLOAD SYN1".."SYN5", "TICKLOAD KIT") and folders match.
  There's one clip per instrument, all `isPlaying="1"`, `section="0"`, `length=384`. The first clip has
  `selected="1"` (Clip::readTagFromFile sets it as currentClip, and the song opens in **song/session view**).
  `--open-clip` writes `beingEdited="1"` instead, which opens that clip's clip view (`inClipMinderViewOnLoad`).
  Session view is the neutral choice for profiling because the UI is the same for the synth and kit variants.
- **Sound params** (`Sound::writeParamsToFile`): the same attribute set as the template's drum `soundParams`.
  Values come from `Sound::setupAsDefaultSynth()`: oscBVolume 0x47AE1457, LPF freq 0x10000000, res 0xA2000000,
  env1 A/D/S, env2, and cables note/env1/velocity->lpfFrequency, velocity->volume. Expression cables come from
  `setupDefaultExpressionPatching`: aftertouch->volume (33, unipolar) and y->lpfFrequency (20). Env1 release and
  volume differ as described above. UI values use `getParamFromUserValue`: `v*85899345 - 2^31`; cables `v*21474836`.
- **Kit** (`Kit::writeDataToFile` / `Kit::readFromFile`): `<kit presetName presetFolder="KITS">` with
  GlobalEffectable attrs, the FX tags, `<soundSources>` holding 20 `<sound name="TLnn" …>` (same body as the synths,
  plus `transpose`), and `<selectedDrumIndex>`. Kit clip: `affectEntire`, `<kitParams>` (GlobalEffectableForClip,
  volume 0 = the `initParams` default; the template's 0x80000000 would be silence). Each `<noteRow>` has
  `colourOffset`, note data, `drumIndex` (index into soundSources) and its own `<soundParams>`, as the template and
  `NoteRow::writeToFile` do for drums. Without per-row soundParams `claimOutput` would have to find a backed-up
  ParamManager.
- **Notes** (`NoteRow::writeToFile` / `readFromFile`, model/note/note_row.cpp): `noteDataWithSplitProb="0x…"`, the
  current format, accepted for any song version. Each note is 28 hex chars:
  `pos(8) length(8) velocity(2) lift(2) probability(2) iterance(4) fill(2)`. We write
  `vel=0x64, lift=0x40 (kDefaultLiftValue), prob=0x14 (kNumProbabilityValues = 100 %), iterance=0000
  (kDefaultIteranceValue = off), fill=00 (FillMode::OFF)`.
  The older encodings are still read: `noteData` (20 chars, pos/len/vel/prob), `noteDataWithLift` (22),
  `noteDataWithIteranceAndFill` (26). The last two are only honoured when the song's firmwareVersion is below c1.3.0.
  The reader drops a note whose pos is below the previous note's end (`minPos`), so notes are sorted and
  non-overlapping. Back-to-back (pos + len == next pos) is fine. Synth note rows use `y` = MIDI note, strictly
  ascending, because the reader clamps `y >= minY`.

## Validation

**Static** (built into the generator, run on every write; `--check` for existing files):
- The XML is well formed (ElementTree).
- Root is `song`, and BPM is recomputed from `timePerTimerTick`/`timerTickFraction` (gives 120.00).
- Every clip has exactly one of soundParams/kitParams. It resolves to an existing instrument of the right type,
  name and folder, with one clip per instrument, and all clips are playing.
- No sample, wavetable or audio-input oscillator and no `fileName`.
- Kit `drumIndex` is within soundSources and every kit row has soundParams. Synth `y` is strictly ascending.
- Note data length is a multiple of 28, notes are sorted, non-overlapping and inside the clip, and the fields are
  the defaults.
- Summary of voices started and ended per tick. All six files show 16 steps with exactly N starts each
  (20/20/10/30/20/20), N ends at the same ticks (gate 1.0), and on average N voices held, release tails excluded.

**Emulator: passed for all six songs** (2026-11-20). Stock firmware `/home/user/perfwork/elf/stock.elf` (md5
5c8c219d…, the same as `/home/user/wt/stock/build/Release/deluge.elf`), delugemu qemu from
`/home/user/synthstromaudible/delugemu/qemu/build`, card built with `/home/user/perfwork/bin/mksd`, no host audio.
Script:

```sh
python3 /home/user/perfwork/song/emu_check.py ELF SD.img /SONGS/TICKLOAD.XML OUTDIR
#   boot (8 s) -> assert_no_aborts -> load_song -> screenshot -> PLAY -> 6 s -> screenshot, LEDs, pads -> assert_no_aborts
#   TL_AUDIO="wav,path=/abs/x.wav" records the SSIF output to a file (QEMU wav audiodev; still no host audio)
```

Results:
- Every song loads without an error popup. The OLED shows the song name: "TICKLOAD", or "TICKLOAD_KIT20" for the
  kit, with "120" BPM and the SESSION_VIEW LED on.
- PLAY LED on, and 0 data/prefetch aborts in the `-d int` log.
- Song view pads: 5 clip rows lit and launched for the synth songs, 1 for the kit.
- An earlier run with `beingEdited` opened clip view of "TICKLOAD SYN1". Its pads showed the chord rows 36/40/43
  (47 off-screen) with a note on all 16 steps and the playhead moving, which confirms note positions and lengths.
- WAV capture of the default song: silence until PLAY, then continuous output, peak -4.8 dBFS. There's no rhythmic
  envelope because gate 1.0 + sustain 50 is nearly continuous. The capture is host-paced, so its timeline isn't
  usable for timing.
- Outputs are in the scratchpad (not kept): `emu_{TICKLOAD,KIT20,N10,N30,FX,U4,flat_KIT20}/`.

The flat layout was re-checked for `/SONGS/TICKLOAD_KIT20.XML`. Phase 1B's notes report the probe autoplaying
`TICKLOAD.XML` from this card. That run had the default synths, no culls, and 60 % load at Deluge speed, so N30
or U4 will be needed to reach culling.
