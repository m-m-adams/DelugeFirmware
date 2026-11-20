#!/usr/bin/env python3
"""[E4 copy of gen_tickload.py: adds per-clip mod FX, delay automation, a mixed synth+kit layout and song/kit level
mod FX, to exercise buffer allocation in the render. Only -o mode is meant to be used; see --e4 options.]

Generate Deluge "TICKLOAD" profiling songs: many voices start together on every sequencer step.

Purpose: stress the song tick (PlaybackHandler::actionTimerTick -> clips -> NoteRows -> Sound::noteOn) on top of
the audio render, with a steady voice count, so the cost of *starting* notes can be separated from the cost of
*rendering* them. See /home/user/perfwork/notes/song.md for the design and where each XML detail comes from.

Song shape (all parameters configurable):
  * 1 bar loop at 120 BPM, 16 sixteenth-note steps, no swing, no arp, no automation.
  * On every step N voices start at the same tick (default N=20). Each note lasts one step (gate 1.0 = 24 ticks,
    the Deluge's default note length for a 16th step) and the amp envelope has the minimum release, so the
    previous step's voices are gone within ~1 render window: steady state is ~N voices, the spike is the starts.
  * Synth-only (subtractive saw + square, 24 dB ladder LPF, default-synth patching), so nothing streams from SD.
  * "synth" layout: C instrument clips (one SoundInstrument each) x K-note chords (N = C*K, or N split over C).
  * "kit" layout: one kit with R synth-type drum rows (no samples), one note per row per step (N = R).
  * FX off by default (no reverb / delay sends); --fx turns on reverb + delay sends on every sound.

Usage:
  gen_tickload.py                      # standard set: SONGS/TICKLOAD.XML + SONGS/TICKLOAD_*.XML
  gen_tickload.py --set --outdir DIR   # standard set into DIR
  gen_tickload.py --stage KIT20 /home/user/perfwork/sdcard_kit20   # card content with only that variant
  gen_tickload.py -o FOO.XML --clips 4 --chord 5 --bpm 140 --fx
  gen_tickload.py -o K.XML --kit 24
  gen_tickload.py --check FILE.XML ...  # static validation + per-step voice-start summary of existing files

Python 3 standard library only.
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET

DEFAULT_OUTDIR = "/home/user/perfwork/sdcard/SONGS"

# --- timing (model/song/song.cpp) -------------------------------------------------------------------------------
# inputTickMagnitude = insideWorldTickMagnitude; FlashStorage::defaultMagnitude = 2 (storage/flash_storage.cpp).
# Song::getBarLength() = increaseMagnitude(96, mag) = 384 ticks, getSixteenthNoteLength() = 6 << 2 = 24 ticks.
INPUT_TICK_MAGNITUDE = 2
TICKS_PER_BAR = 96 << INPUT_TICK_MAGNITUDE  # 384
TICKS_PER_16TH = 6 << INPUT_TICK_MAGNITUDE  # 24

# --- note encoding (model/note/note_row.cpp NoteRow::writeToFile / readFromFile) ----------------------------------
# "noteDataWithSplitProb" = "0x" + per note 28 hex chars:
#   pos(8) length(8) velocity(2) lift(2) probability(2) iterance(4) fill(2)
# probability 20 = kNumProbabilityValues (100%), lift 64 = kDefaultLiftValue, iterance 0 = kDefaultIteranceValue
# (off), fill 0 = FillMode::OFF (definitions_cxx.hpp). Accepted regardless of the song's firmwareVersion.
NOTE_HEX_LEN = 28
LIFT_DEFAULT = 64
PROB_100 = 20


def hex32(v):
    return "0x%08X" % (v & 0xFFFFFFFF)


def user_to_param(v):
    """UI 0..50 -> param value; util/functions.cpp getParamFromUserValue default branch."""
    return (v * 85899345 - 2147483648) & 0xFFFFFFFF


def cable_amount(v):
    """Patch cable UI -50..50 -> amount; getParamFromUserValue(PATCH_CABLE)."""
    return v * 21474836


def note_hex(pos, length, vel):
    s = "%08X%08X%02X%02X%02X%04X%02X" % (pos, length, vel, LIFT_DEFAULT, PROB_100, 0, 0)
    assert len(s) == NOTE_HEX_LEN
    return s


def time_per_timer_tick(bpm):
    """Song::setBPMInner: samples per internal tick = 110250 / (bpm * 2^mag), stored as 32.32 fixed point split
    into timePerTimerTick (int part) and timerTickFraction (low 32 bits, written as a signed int32)."""
    t = 110250.0 / (bpm * (1 << INPUT_TICK_MAGNITUDE))
    big = int(round(t * 4294967296.0))
    ipart = big >> 32
    frac = big & 0xFFFFFFFF
    if frac >= 0x80000000:
        frac -= 0x100000000
    return ipart, frac


# ------------------------------------------------------------------------------------------------------------------
# XML fragments. Structure, tag names and attribute sets follow what the firmware writes (Song::writeToFile,
# SoundInstrument/Sound::writeToFile, Kit::writeDataToFile, InstrumentClip::writeDataToFile, NoteRow::writeToFile),
# cross-checked against contrib/audio_export/multi_sample/Multisample_Template_Full_Bar.XML (a real c1.3.0 song
# with a kit of 128 synth-type drums).
# ------------------------------------------------------------------------------------------------------------------

ARP_ATTRS = """mode="off"
{i}	syncLevel="7"
{i}	numOctaves="2"
{i}	syncType="0"
{i}	arpMode="off"
{i}	noteMode="up"
{i}	octaveMode="up\""""

FX_TAGS = """{i}<delay
{i}	pingPong="1"
{i}	analog="0"
{i}	syncLevel="7"
{i}	syncType="0" />
{i}<sidechain
{i}	attack="327244"
{i}	release="936"
{i}	syncLevel="6"
{i}	syncType="0" />
{i}<audioCompressor
{i}	attack="83886080"
{i}	release="83886080"
{i}	thresh="0"
{i}	ratio="1073741824"
{i}	compHPF="0"
{i}	compBlend="2147483647" />
{i}<stutter
{i}	quantized="1"
{i}	reverse="0"
{i}	pingPong="0" />
"""

MOD_KNOBS = [
    ('pan', None, None), ('volumePostFX', None, None), ('lpfResonance', None, None), ('lpfFrequency', None, None),
    ('env1Release', None, None), ('env1Attack', None, None), ('delayFeedback', None, None), ('delayRate', None, None),
    ('reverbAmount', None, None), ('volumePostReverbSend', 'compressor', None), ('pitch', 'lfo1', None),
    ('lfo1Rate', None, None), ('portamento', None, None), ('stutterRate', None, None), ('bitcrushAmount', None, None),
    ('volume', 'velocity', 'envelope1'),
]

EQ_TAG = """{i}<equalizer
{i}	bass="0x00000000"
{i}	treble="0x00000000"
{i}	bassFrequency="0x00000000"
{i}	trebleFrequency="0x00000000" />
"""


def ind(n):
    return "\t" * n


def mod_knobs_xml(i):
    out = [i + "<modKnobs>\n"]
    for param, src, src2 in MOD_KNOBS:
        a = 'controlsParam="%s"' % param
        if src:
            a += ' patchAmountFromSource="%s"' % src
        if src2:
            a += ' patchAmountFromSecondSource="%s"' % src2
        out.append(i + "\t<modKnob %s />\n" % a)
    out.append(i + "</modKnobs>\n")
    return "".join(out)


def sound_body_xml(i, cfg, transpose=0, modfx="none"):
    """Attributes after the identity ones + child tags of a <sound> (Sound::writeToFile), for a SoundInstrument at
    song level or a SoundDrum inside a kit. No <defaultParams>: in a song the params live in the clip's
    <soundParams> (synth) or the NoteRow's <soundParams> (kit drum)."""
    s = ""
    s += i + '\tpolyphonic="poly"\n'
    s += i + '\tvoicePriority="1"\n'
    s += i + '\tmode="subtractive"\n'
    if transpose:
        s += i + '\ttranspose="%d"\n' % transpose
    s += i + '\tmodFXType="%s"\n' % modfx
    s += i + '\tlpfMode="24dB"\n'
    s += i + '\thpfMode="HPLadder"\n'
    s += i + '\tfilterRoute="H2L"\n'
    s += i + '\tmaxVoices="%d">\n' % cfg.max_voices
    s += i + '\t<osc1\n'
    s += i + '\t\ttype="%s"\n' % cfg.osc1
    s += i + '\t\ttranspose="0"\n'
    s += i + '\t\tcents="0"\n'
    s += i + '\t\tretrigPhase="-1" />\n'
    s += i + '\t<osc2\n'
    s += i + '\t\ttype="%s"\n' % cfg.osc2
    s += i + '\t\ttranspose="-12"\n'
    s += i + '\t\tcents="0"\n'
    s += i + '\t\tretrigPhase="-1" />\n'
    for n in range(1, 5):
        s += i + '\t<lfo%d type="triangle" syncLevel="0" syncType="0" />\n' % n
    s += i + '\t<unison num="%d" detune="%d" spread="0" />\n' % (cfg.unison, 10 if cfg.unison > 1 else 8)
    s += mod_knobs_xml(i + "\t")
    s += FX_TAGS.format(i=i + "\t")
    return s


def sound_params_xml(i, tag, cfg, delay_auto=False, fx_on=True, rate_auto=False):
    """<soundParams> (Sound::writeParamsToFile). Values: Sound::setupAsDefaultSynth() (osc B level, LPF, envelopes,
    patch cables) except env1 release (cfg.release, default 0 = shortest) and volume; the rest are the neutral
    values the firmware writes (as in the template song's drum soundParams)."""
    rev = hex32(user_to_param(cfg.fx_reverb)) if cfg.fx else "0x80000000"
    dfb = hex32(user_to_param(cfg.fx_delay)) if (cfg.fx and fx_on) else "0x80000000"
    if delay_auto and cfg.fx:
        # step automation: delay on for the first half of the bar, off (min) for the second half -> the Delay is
        # discarded and re-allocated every bar in the render
        half = cfg.bars * TICKS_PER_BAR // 2
        dfb = dfb + "%s%08X" % (dfb[2:], 0) + "%08X%08X" % (0x80000000, half)
    mfd = "0x20000000" if cfg.e4 else "0x00000000"
    drate = "0x00000000"
    if rate_auto:
        # delay rate steps every quarter bar: 0, +0x40000000, 0, -0x40000000 -> new secondary delay buffers in the render
        q = cfg.bars * TICKS_PER_BAR // 4
        drate = "0x00000000" + "".join("%08X%08X" % (v & 0xFFFFFFFF, k * q)
                                       for k, v in enumerate([0, 0x40000000, 0, -0x40000000]))
    osc_b = "0x80000000" if getattr(cfg, "osc2_muted", False) else "0x47AE1457"
    attrs = [
        ("portamento", "0x80000000"), ("compressorShape", "0xDC28F5B2"),
        ("oscAVolume", "0x7FFFFFFF"), ("oscAPulseWidth", "0x00000000"), ("oscAWavetablePosition", "0x00000000"),
        ("oscBVolume", osc_b), ("oscBPulseWidth", "0x00000000"), ("oscBWavetablePosition", "0x00000000"),
        ("noiseVolume", "0x80000000"), ("volume", hex32(cfg.volume)), ("pan", "0x00000000"),
        ("lpfFrequency", "0x10000000"), ("lpfResonance", "0xA2000000"),
        ("hpfFrequency", "0x80000000"), ("hpfResonance", "0x80000000"),
        ("lfo1Rate", "0x1999997E"), ("lfo2Rate", "0x00000000"), ("lfo3Rate", "0x1999997E"), ("lfo4Rate", "0x00000000"),
        ("modulator1Amount", "0x80000000"), ("modulator1Feedback", "0x80000000"),
        ("modulator2Amount", "0x80000000"), ("modulator2Feedback", "0x80000000"),
        ("carrier1Feedback", "0x80000000"), ("carrier2Feedback", "0x80000000"),
        ("modFXRate", "0x00000000"), ("modFXDepth", mfd),
        ("delayRate", drate), ("delayFeedback", dfb), ("reverbAmount", rev),
        ("arpeggiatorRate", "0x00000000"), ("stutterRate", "0x00000000"),
        ("sampleRateReduction", "0x80000000"), ("bitCrush", "0x80000000"),
        ("modFXOffset", "0x00000000"), ("modFXFeedback", "0x00000000"), ("compressorThreshold", "0x00000000"),
        ("arpeggiatorGate", "0x00000000"), ("noteProbability", "0x7FFFFFFF"),
        ("lpfMorph", "0x80000000"), ("hpfMorph", "0x80000000"), ("waveFold", "0x80000000"),
    ]
    s = i + "<%s\n" % tag
    s += "".join(i + '\t%s="%s"\n' % kv for kv in attrs[:-1])
    s += i + '\t%s="%s">\n' % attrs[-1]
    j = i + "\t"
    envs = [
        ("0x80000000", "0xE6666654", "0x7FFFFFFF", hex32(user_to_param(cfg.release))),
        ("0xA3D70A37", "0xA3D70A37", "0xFFFFFFE9", "0xE6666654"),
        ("0x00000000", "0x00000000", "0x00000000", "0x00000000"),
        ("0x00000000", "0x00000000", "0x00000000", "0x00000000"),
    ]
    for n, (a, d, su, r) in enumerate(envs, 1):
        s += j + "<envelope%d\n" % n
        s += j + '\tattack="%s"\n' % a
        s += j + '\tdecay="%s"\n' % d
        s += j + '\tsustain="%s"\n' % su
        s += j + '\trelease="%s" />\n' % r
    cables = [  # Sound::setupAsDefaultSynth + setupDefaultExpressionPatching
        ("note", "lpfFrequency", "bipolar", 0x08F5C28C),
        ("envelope1", "lpfFrequency", "bipolar", 0x1C28F5B8),
        ("velocity", "lpfFrequency", "bipolar", 0x0F5C28F0),
        ("velocity", "volume", "bipolar", 0x3FFFFFE8),
        ("aftertouch", "volume", "unipolar", cable_amount(33)),
        ("y", "lpfFrequency", "bipolar", cable_amount(20)),
    ]
    s += j + "<patchCables>\n"
    for src, dst, pol, amt in cables:
        s += j + "\t<patchCable\n"
        s += j + '\t\tsource="%s"\n' % src
        s += j + '\t\tdestination="%s"\n' % dst
        s += j + '\t\tpolarity="%s"\n' % pol
        s += j + '\t\tamount="%s" />\n' % hex32(amt)
    s += j + "</patchCables>\n"
    s += EQ_TAG.format(i=j)
    s += i + "</%s>\n" % tag
    return s


def global_params_attrs(i, volume_hex, reverb_hex="0x80000000", tempo_hex="0x00000000", modfx_fb="0x80000000",
                        modfx_depth="0x00000000", delay_fb="0x80000000"):
    """GlobalEffectableForClip::writeParamAttributesToFile attribute set (songParams / kitParams)."""
    attrs = [
        ("reverbAmount", reverb_hex), ("volume", volume_hex), ("pan", "0x00000000"),
        ("sidechainCompressorShape", "0xDC28F5B2"), ("modFXDepth", modfx_depth), ("modFXRate", "0xE0000000"),
        ("stutterRate", "0x00000000"), ("sampleRateReduction", "0x80000000"), ("bitCrush", "0x80000000"),
        ("modFXOffset", "0x00000000"), ("modFXFeedback", modfx_fb), ("compressorThreshold", "0x00000000"),
        ("arpeggiatorGate", "0x7FFFFFFF"), ("noteProbability", "0x7FFFFFFF"),
        ("lpfMorph", "0x80000000"), ("hpfMorph", "0x80000000"),
        ("tempo", tempo_hex), ("arpeggiatorRate", "0x00000000"),
    ]
    s = "".join(i + '\t%s="%s"\n' % kv for kv in attrs[:-1])
    s += i + '\t%s="%s">\n' % attrs[-1]
    j = i + "\t"
    s += j + '<delay\n' + j + '\trate="0x00000000"\n' + j + '\tfeedback="%s" />\n' % delay_fb
    s += j + '<lpf\n' + j + '\tfrequency="0x7FFFFFFF"\n' + j + '\tresonance="0x80000000" />\n'
    s += j + '<hpf\n' + j + '\tfrequency="0x80000000"\n' + j + '\tresonance="0x80000000" />\n'
    s += EQ_TAG.format(i=j)
    return s


GLOBAL_EFFECTABLE_ATTRS = """{i}	modFXCurrentParam="feedback"
{i}	currentFilterType="lpf"
{i}	modFXType="{modfx}"
{i}	lpfMode="24dB"
{i}	hpfMode="HPLadder"
{i}	filterRoute="H2L\""""


def chord_notes(base, k):
    """k ascending MIDI notes from base, stacked major/minor thirds (fall back to semitones near the top)."""
    notes, n, steps = [], base, (4, 3)
    for j in range(k):
        notes.append(n)
        n += steps[j % 2]
    if notes[-1] > 120:
        notes = list(range(base, base + k))
    assert notes[-1] <= 127, "too many notes per chord"
    return notes


def step_positions(cfg):
    """Tick positions of the steps that start notes."""
    total = cfg.bars * TICKS_PER_BAR
    return [p for p in range(0, total, cfg.step_ticks)]


def note_row_data(cfg):
    length = max(1, int(round(cfg.step_ticks * cfg.gate)))
    return "0x" + "".join(note_hex(p, length, cfg.velocity) for p in step_positions(cfg))


# ------------------------------------------------------------------------------------------------------------------

def build_song(cfg):
    ipart, frac = time_per_timer_tick(cfg.bpm)
    clip_len = cfg.bars * TICKS_PER_BAR
    o = []
    w = o.append
    w('<?xml version="1.0" encoding="UTF-8"?>\n')
    w("<song\n")
    w('\tfirmwareVersion="c1.3.0"\n')
    w('\tearliestCompatibleFirmware="4.1.0-alpha"\n')
    w('\tarrangementAutoScrollOn="0"\n')
    w('\txScroll="0"\n')
    w('\txZoom="%d"\n' % (clip_len // 16))
    nclips = (len(cfg.chords) + (1 if cfg.e4_kit else 0)) if cfg.layout == "synth" else 1
    w('\tyScrollSongView="%d"\n' % max(-7, nclips - 8))  # clips fill the top rows of song view
    w('\tyScrollArrangementView="-6"\n')
    w('\txScrollArrangementView="0"\n')
    w('\txZoomArrangementView="192"\n')
    w('\ttimePerTimerTick="%d"\n' % ipart)
    w('\ttimerTickFraction="%d"\n' % frac)
    w('\trootNote="0"\n')
    w('\tinputTickMagnitude="%d"\n' % INPUT_TICK_MAGNITUDE)
    w('\tswingAmount="0"\n')
    w('\tswingInterval="7"\n')  # file value for 16ths at magnitude 2 (Song::convertSyncLevelFromFileValue...)
    w('\taffectEntire="0"\n')
    w('\tactiveModFunction="0"\n')
    w(GLOBAL_EFFECTABLE_ATTRS.format(i="", modfx=cfg.song_modfx) + "\n")
    w('\tsongGridScrollX="0"\n')
    w('\tsongGridScrollY="0"\n')
    w('\tsessionLayout="0">\n')
    w("\t<modeNotes>\n")
    for n in (0, 2, 4, 5, 7, 9, 11):
        w("\t\t<modeNote>%d</modeNote>\n" % n)
    w("\t</modeNotes>\n")
    w('\t<reverb\n\t\troomSize="1288489856"\n\t\tdampening="1546188288"\n\t\twidth="2147483647"\n'
      '\t\thpf="0"\n\t\tlpf="2147483647"\n\t\tpan="0"\n\t\tmodel="1">\n')
    w('\t\t<compressor\n\t\t\tattack="0"\n\t\t\trelease="0"\n\t\t\tvolume="-21474836"\n'
      '\t\t\tshape="-601295438"\n\t\t\tsyncLevel="2" />\n')
    w("\t</reverb>\n")
    w(FX_TAGS.format(i="\t"))
    w("\t<songParams\n")
    w(global_params_attrs("\t", "0x00000000", tempo_hex=hex32(int(round(cfg.bpm * 100))),
                          modfx_fb=("0x00000000" if cfg.song_modfx != "none" else "0x80000000"),
                          modfx_depth=("0x20000000" if cfg.song_modfx != "none" else "0x00000000")))
    w("\t</songParams>\n")

    # ---- instruments ----
    w("\t<instruments>\n")
    if cfg.layout == "synth":
        for c, k in enumerate(cfg.chords):
            w("\t\t<sound\n")
            w('\t\t\tpresetName="%s"\n' % synth_name(c))
            w('\t\t\tpresetFolder="SYNTHS"\n')
            w('\t\t\tdefaultVelocity="100"\n')
            w('\t\t\tisArmedForRecording="0"\n')
            w('\t\t\tactiveModFunction="0"\n')
            w(sound_body_xml("\t\t", cfg, modfx=clip_modfx(cfg, c)))
            w("\t\t</sound>\n")
    if cfg.layout == "kit" or cfg.e4_kit:
        w("\t\t<kit\n")
        w('\t\t\tpresetName="%s"\n' % KIT_NAME)
        w('\t\t\tpresetFolder="KITS"\n')
        w('\t\t\tdefaultVelocity="100"\n')
        w('\t\t\tisArmedForRecording="0"\n')
        w('\t\t\tactiveModFunction="0"\n')
        w(GLOBAL_EFFECTABLE_ATTRS.format(i="\t\t", modfx=cfg.kit_modfx) + ">\n")
        w(FX_TAGS.format(i="\t\t\t"))
        w("\t\t\t<soundSources>\n")
        for r in range(cfg.kit_rows):
            w("\t\t\t\t<sound\n")
            w('\t\t\t\t\tname="%s"\n' % drum_name(r))
            # spread drum pitches (drums play at note 60 + transpose) for an audible, non-unison texture
            w(sound_body_xml("\t\t\t\t", cfg, transpose=kit_transpose(r), modfx=clip_modfx(cfg, r + 2)))
            w("\t\t\t\t</sound>\n")
        w("\t\t\t</soundSources>\n")
        w("\t\t\t<selectedDrumIndex>0</selectedDrumIndex>\n")
        w("\t\t</kit>\n")
    w("\t</instruments>\n")

    w("\t<sections>\n")
    for s in range(24):
        w('\t\t<section id="%d" numRepeats="0" />\n' % s)
    w("\t</sections>\n")

    # ---- clips ----
    data = note_row_data(cfg)
    w("\t<sessionClips>\n")
    clips = [(c, k, False) for c, k in enumerate(cfg.chords)] if cfg.layout == "synth" else [(0, cfg.kit_rows, True)]
    if cfg.layout == "synth" and cfg.e4_kit:
        clips.append((len(cfg.chords), cfg.e4_kit, True))
    for c, k, is_kit in clips:
        w("\t\t<instrumentClip\n")
        w('\t\t\tclipName=""\n')
        w('\t\t\tinKeyMode="0"\n')
        w('\t\t\tyScroll="%d"\n' % (0 if is_kit else 36))
        w('\t\t\tyScrollKeyboard="50"\n')
        if is_kit:
            w('\t\t\taffectEntire="1"\n')
        w('\t\t\tinstrumentPresetName="%s"\n' % (KIT_NAME if is_kit else synth_name(c)))
        w('\t\t\tinstrumentPresetFolder="%s"\n' % ("KITS" if is_kit else "SYNTHS"))
        w('\t\t\tisPlaying="1"\n')
        w('\t\t\tisSoloing="0"\n')
        w('\t\t\tisArmedForRecording="0"\n')
        w('\t\t\tlength="%d"\n' % clip_len)
        w('\t\t\tcolourOffset="%d"\n' % ((c * 23) % 64 - 32))
        w('\t\t\tsection="0"\n')
        if c == 0:
            # Clip::writeDataToFile: "beingEdited" when saved from clip view (song opens in that clip),
            # "selected" when saved from song view (song opens in session view). Session view is the neutral
            # choice for profiling (same UI for synth and kit variants).
            w('\t\t\t%s="1"\n' % ("beingEdited" if cfg.open_clip else "selected"))
        w('\t\t\tkeyboardLayout="0"\n')
        w('\t\t\tkeyboardRowInterval="5"\n')
        w('\t\t\tdrumsScrollOffset="0"\n')
        w('\t\t\tdrumsZoomLevel="8"\n')
        w('\t\t\tinKeyScrollOffset="0"\n')
        w('\t\t\tinKeyRowInterval="3">\n')
        w("\t\t\t<arpeggiator\n\t\t\t\t" + ARP_ATTRS.format(i="\t\t\t") + " />\n")
        if is_kit:
            w("\t\t\t<kitParams\n")
            if cfg.kit_modfx != "none":
                half = cfg.bars * TICKS_PER_BAR // 2
                # kit mod FX feedback: on for the first half bar, at minimum for the second -> getActiveModFXType()
                # goes NONE and back, so the kit's mod FX buffer is freed and re-allocated in the render every bar
                fb = "0x00000000" + "%08X%08X%08X%08X" % (0, 0, 0x80000000, half)
            else:
                fb = "0x80000000"
            w(global_params_attrs("\t\t\t", "0x00000000",
                                  reverb_hex="0x80000000", modfx_fb=fb,
                                  modfx_depth=("0x20000000" if cfg.kit_modfx != "none" else "0x00000000"),
                                  delay_fb=(hex32(user_to_param(cfg.fx_delay)) if cfg.fx else "0x80000000")))
            w("\t\t\t</kitParams>\n")
        else:
            w(sound_params_xml("\t\t\t", "soundParams", cfg, delay_auto=(c < cfg.delay_auto_clips),
                               rate_auto=(c >= len(cfg.chords) - cfg.delay_rate_auto_clips)))
        w("\t\t\t<columnControls>\n\t\t\t\t<leftCol\n\t\t\t\t\ttype=\"velocity\" />\n"
          "\t\t\t\t<rightCol\n\t\t\t\t\ttype=\"mod\" />\n\t\t\t</columnControls>\n")
        w("\t\t\t<noteRows>\n")
        if is_kit:
            for r in range(k):
                w("\t\t\t\t<noteRow\n")
                w('\t\t\t\t\tcolourOffset="%d"\n' % ((r * 7) % 64))
                w('\t\t\t\t\tnoteDataWithSplitProb="%s"\n' % (kit_row_data(cfg) if cfg.layout == "synth" else data))
                w('\t\t\t\t\tdrumIndex="%d">\n' % r)
                w(sound_params_xml("\t\t\t\t\t", "soundParams", cfg))
                w("\t\t\t\t</noteRow>\n")
        else:
            for y in chord_notes(clip_base_note(c), k):
                w("\t\t\t\t<noteRow\n")
                w('\t\t\t\t\ty="%d"\n' % y)
                w('\t\t\t\t\tnoteDataWithSplitProb="%s" />\n' % data)
        w("\t\t\t</noteRows>\n")
        w("\t\t</instrumentClip>\n")
    w("\t</sessionClips>\n")
    w("</song>\n")
    return "".join(o)


KIT_NAME = "TICKLOAD KIT"

MODFX_CYCLE = ["chorus", "flanger", "phaser", "grainFX", "TapeWarble", "dimension", "StereoChorus"]


def clip_modfx(cfg, c):
    if not cfg.e4:
        return "none"
    return MODFX_CYCLE[c % len(MODFX_CYCLE)]


def kit_row_data(cfg):
    """E4 mixed layout: kit rows hit once per beat (sparse), so drum delays decay and get discarded between hits."""
    length = max(1, int(round(TICKS_PER_16TH * cfg.gate)))
    return "0x" + "".join(note_hex(p, length, cfg.velocity) for p in range(0, cfg.bars * TICKS_PER_BAR, 4 * TICKS_PER_16TH))


def synth_name(c):
    return "TICKLOAD SYN%d" % (c + 1)


def drum_name(r):
    return "TL%02d" % (r + 1)


def kit_transpose(r):
    return (r * 5) % 24 - 12


def clip_base_note(c):
    return 36 + (c * 7) % 24


# ------------------------------------------------------------------------------------------------------------------
# Static validation: re-parse the file the way the firmware would read the parts that matter.
# ------------------------------------------------------------------------------------------------------------------

def parse_note_data(s):
    assert s.startswith("0x"), "note data must start with 0x"
    s = s[2:]
    assert len(s) % NOTE_HEX_LEN == 0, "note data length %d not a multiple of %d" % (len(s), NOTE_HEX_LEN)
    notes = []
    for i in range(0, len(s), NOTE_HEX_LEN):
        h = s[i:i + NOTE_HEX_LEN]
        notes.append(dict(pos=int(h[0:8], 16), length=int(h[8:16], 16), vel=int(h[16:18], 16),
                          lift=int(h[18:20], 16), prob=int(h[20:22], 16), iterance=int(h[22:26], 16),
                          fill=int(h[26:28], 16)))
    return notes


def check_song(path, quiet=False, table=False):
    errs = []
    tree = ET.parse(path)  # well-formed XML
    root = tree.getroot()
    if root.tag != "song":
        errs.append("root tag is %r, not 'song'" % root.tag)
    mag = int(root.get("inputTickMagnitude", "-99"))
    bar = 96 << mag
    tpt = int(root.get("timePerTimerTick")) + (int(root.get("timerTickFraction")) & 0xFFFFFFFF) / 4294967296.0
    bpm = 110250.0 / tpt / (1 << mag)
    if int(root.get("swingAmount", "0")) != 0:
        errs.append("swing is on")

    insts = {}
    for el in root.find("instruments"):
        if el.tag == "sound":
            insts[("synth", el.get("presetName"), el.get("presetFolder", "SYNTHS"))] = el
        elif el.tag == "kit":
            insts[("kit", el.get("presetName"), el.get("presetFolder", "KITS"))] = el
        else:
            errs.append("unexpected instrument tag %s" % el.tag)
        for osc in el.iter():
            if osc.tag in ("osc1", "osc2") and osc.get("type") in ("sample", "wavetable", "inLeft", "inRight"):
                errs.append("%s uses %s (needs SD/audio input)" % (el.get("presetName"), osc.get("type")))
            if osc.get("fileName"):
                errs.append("file reference %s" % osc.get("fileName"))
    if len(insts) != len(root.find("instruments")):
        errs.append("duplicate instrument names")

    starts = {}       # tick -> voices started
    ends = {}         # tick -> voices ended
    per_clip = []
    total_voice_ticks = 0
    used = set()
    for clip in root.find("sessionClips"):
        if clip.tag != "instrumentClip":
            errs.append("unexpected clip tag %s" % clip.tag)
            continue
        length = int(clip.get("length"))
        is_kit = clip.find("kitParams") is not None
        is_syn = clip.find("soundParams") is not None
        if is_kit == is_syn:
            errs.append("clip must have exactly one of kitParams/soundParams (sets outputTypeWhileLoading)")
        key = ("kit" if is_kit else "synth", clip.get("instrumentPresetName"), clip.get("instrumentPresetFolder"))
        if key not in insts:
            errs.append("clip refers to missing instrument %r" % (key,))
            continue
        if key in used:
            errs.append("two clips on one instrument %r (only one would play)" % (key,))
        used.add(key)
        if clip.get("isPlaying") != "1":
            errs.append("clip %r not playing" % (key,))
        rows = clip.find("noteRows")
        nrows = 0
        last_y = -10**9
        ndrums = len(insts[key].find("soundSources")) if is_kit else 0
        for row in rows:
            nrows += 1
            if is_kit:
                di = int(row.get("drumIndex"))
                if not (0 <= di < ndrums):
                    errs.append("drumIndex %d out of range" % di)
                if row.find("soundParams") is None:
                    errs.append("kit row without soundParams")
            else:
                y = int(row.get("y"))
                if y <= last_y:
                    errs.append("noteRow y not strictly ascending (%d after %d)" % (y, last_y))
                last_y = y
            data = row.get("noteDataWithSplitProb")
            if data is None:
                errs.append("row without noteDataWithSplitProb")
                continue
            min_pos = 0
            for n in parse_note_data(data):
                if n["pos"] < min_pos:
                    errs.append("overlapping/unsorted notes at %d (firmware would drop it)" % n["pos"])
                if n["pos"] + n["length"] > length:
                    errs.append("note at %d len %d runs past clip length %d" % (n["pos"], n["length"], length))
                if not (1 <= n["vel"] <= 127) or n["prob"] != PROB_100 or n["iterance"] or n["fill"]:
                    errs.append("odd note fields %r" % n)
                min_pos = n["pos"] + n["length"]
                starts[n["pos"]] = starts.get(n["pos"], 0) + 1
                e = (n["pos"] + n["length"]) % length
                ends[e] = ends.get(e, 0) + 1
                total_voice_ticks += n["length"]
        per_clip.append((key[1], "kit" if is_kit else "synth", nrows))
        clip_len = length

    ticks = sorted(set(starts) | set(ends))
    if not quiet:
        print("%s: %.2f BPM, %d ticks/bar, %d clip(s): %s" % (
            os.path.basename(path), bpm, bar, len(per_clip),
            ", ".join("%s[%s x%d]" % c for c in per_clip)))
        if table:
            print("  tick : starts ends")
        for t in (ticks if table else []):
            print("  %4d : %5d %4d   (step %g)" % (t, starts.get(t, 0), ends.get(t, 0), t / (bar / 16.0)))
        avg = total_voice_ticks / float(clip_len)
        print("  steps with starts: %d, voices started per such step: min %d max %d, avg held voices %.1f "
              "(excluding release tails)" % (len(starts), min(starts.values()), max(starts.values()), avg))
    if errs:
        for e in errs:
            print("  ERROR:", e)
    elif not quiet:
        print("  OK")
    return not errs


# ------------------------------------------------------------------------------------------------------------------

class Cfg:
    pass


def make_cfg(a, **over):
    c = Cfg()
    for k, v in vars(a).items():
        setattr(c, k, v)
    for k, v in over.items():
        setattr(c, k, v)
    c.step_ticks = TICKS_PER_16TH * c.step_16ths
    if c.kit:
        c.layout = "kit"
        c.kit_rows = c.kit
        c.chords = None
        per_sound = 1
    else:
        c.layout = "synth"
        if c.voices is not None:
            n, cl = c.voices, c.clips
            c.chords = [n // cl + (1 if i < n % cl else 0) for i in range(cl)]
            c.chords = [k for k in c.chords if k > 0]
        else:
            c.chords = [c.chord] * c.clips
        per_sound = max(c.chords)
        c.kit_rows = c.e4_kit
    if c.max_voices is None:
        # Room for this step's notes plus the previous step's (in fast release) so the per-Sound voice limit
        # (Sound::acquireVoice -> terminateOneActiveVoice) never steals. The UI allows up to 16.
        c.max_voices = max(8, min(16, 2 * per_sound)) if per_sound <= 8 else min(255, 2 * per_sound)
    return c


# Card layout: all songs directly in /SONGS. The probe (wt/probe dz_probe.cpp) autoplays the strcasecmp-smallest
# *.XML directly in /SONGS whose name contains dzSongPath (default "TICKLOAD"; patch to e.g. "KIT20" with
# perfwork/tools/set_song.py). '.' < '_' so TICKLOAD.XML wins for "TICKLOAD", and every variant suffix is unique.
# FAT directory order is NOT usable for this (mksd copies in ext4 hash order); see notes/song.md.
VARIANT_SUBDIR = ""
OLD_VARIANT_SUBDIR = "VARIANTS"  # layout of an earlier version of this script; cleaned up on --set

STANDARD_SET = [
    # (filename, subdir, overrides, description)
    ("TICKLOAD.XML", "", dict(), "default: 5 synth clips x 4-note chords = 20 voices/step"),
    ("TICKLOAD_KIT20.XML", VARIANT_SUBDIR, dict(kit=20), "1 kit, 20 synth drum rows = 20 voices/step"),
    ("TICKLOAD_N10.XML", VARIANT_SUBDIR, dict(chord=2), "5 synth clips x 2 notes = 10 voices/step"),
    ("TICKLOAD_N30.XML", VARIANT_SUBDIR, dict(chord=6), "5 synth clips x 6 notes = 30 voices/step"),
    ("TICKLOAD_FX.XML", VARIANT_SUBDIR, dict(fx=True), "default + reverb/delay sends on every sound"),
    ("TICKLOAD_U4.XML", VARIANT_SUBDIR, dict(unison=4),
     "default with unison 4 (firmware's default-synth unison): heavier render"),
]


def find_standard(name):
    key = name.upper()
    for entry in STANDARD_SET:
        stem = entry[0][:-4]
        if key in (entry[0], stem, stem.replace("TICKLOAD_", "", 1), "DEFAULT" if stem == "TICKLOAD" else None):
            return entry
    raise SystemExit("unknown variant %r; one of: %s" % (name, ", ".join(e[0] for e in STANDARD_SET)))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--output", help="write one song to this path (otherwise the standard set)")
    p.add_argument("--set", action="store_true", help="write the standard set (default when no -o)")
    p.add_argument("--outdir", default=DEFAULT_OUTDIR, help="directory for the standard set [%(default)s]")
    p.add_argument("--check", nargs="+", metavar="XML", help="only validate existing song files")
    p.add_argument("--stage", nargs=2, metavar=("VARIANT", "DIR"),
                   help="write card content DIR/SONGS/<variant>.XML holding only that standard variant (e.g. KIT20, "
                        "N30, DEFAULT), for probe autoplay; build it with /home/user/perfwork/bin/mksd DIR IMG")
    p.add_argument("-v", "--verbose", action="store_true", help="print the per-tick start/end table")
    g = p.add_argument_group("layout")
    g.add_argument("--clips", type=int, default=5, help="synth clips (one synth each) [%(default)s]")
    g.add_argument("--chord", type=int, default=4, help="notes per chord per clip [%(default)s]")
    g.add_argument("--voices", type=int, default=None,
                   help="total voices per step, split over --clips (overrides --chord)")
    g.add_argument("--kit", type=int, default=0, metavar="ROWS",
                   help="kit layout: one kit with ROWS synth drum rows instead of synth clips")
    g = p.add_argument_group("timing")
    g.add_argument("--bpm", type=float, default=120.0, help="[%(default)s]")
    g.add_argument("--bars", type=int, default=1, help="clip length in bars [%(default)s]")
    g.add_argument("--step-16ths", type=int, default=1,
                   help="start notes every this many 16ths [%(default)s]")
    g.add_argument("--gate", type=float, default=1.0,
                   help="note length as a fraction of the step (1.0 = back-to-back) [%(default)s]")
    g = p.add_argument_group("sound")
    g.add_argument("--velocity", type=int, default=100)
    g.add_argument("--osc1", default="saw", help="osc1 type: saw, square, analogSaw, sine, triangle... [saw]")
    g.add_argument("--osc2", default="square", help="osc2 type, or 'off' to mute osc B [square]")
    g.add_argument("--unison", type=int, default=1, help="unison voices per note (1..8) [%(default)s]")
    g.add_argument("--release", type=int, default=0, help="amp env release, UI 0..50 [%(default)s]")
    g.add_argument("--volume", type=lambda s: int(s, 0), default=0x30000000,
                   help="per-sound volume param (default-synth is 0x50000000) [0x30000000]")
    g.add_argument("--max-voices", type=int, default=None, help="per-sound voice limit (default: 2x chord, >=8)")
    g.add_argument("--open-clip", action="store_true",
                   help="song opens in the first clip's clip view instead of song (session) view")
    g.add_argument("--fx", action="store_true", help="turn on reverb + delay sends on every sound")
    g.add_argument("--fx-reverb", type=int, default=15, help="reverb send UI value with --fx [%(default)s]")
    g.add_argument("--fx-delay", type=int, default=15, help="delay feedback UI value with --fx [%(default)s]")
    g = p.add_argument_group("E4 (deferred allocation) options")
    g.add_argument("--e4", action="store_true", help="per-sound mod FX cycling chorus/flanger/phaser/grain/warble/...")
    g.add_argument("--e4-kit", type=int, default=0, metavar="ROWS",
                   help="also add a kit clip with ROWS synth drums (one hit per beat), to the synth layout")
    g.add_argument("--kit-modfx", default="none", help="kit-level mod FX type (GlobalEffectable: allocated in render)")
    g.add_argument("--song-modfx", default="none", help="song-level mod FX type")
    g.add_argument("--delay-rate-auto-clips", type=int, default=0,
                   help="last N synth clips get delay rate step automation (quarter-bar steps)")
    g.add_argument("--delay-auto-clips", type=int, default=0,
                   help="first N synth clips get delay feedback step automation (on half bar / off half bar)")
    a = p.parse_args(argv)

    if a.check:
        ok = all([check_song(f, table=True) for f in a.check])
        return 0 if ok else 1

    jobs = []
    if a.stage:
        name, sub, over, desc = find_standard(a.stage[0])
        songs = os.path.join(a.stage[1], "SONGS")
        os.makedirs(songs, exist_ok=True)
        stale = [f for f in os.listdir(songs) if "TICKLOAD" in f.upper() and f.upper() != name]
        if stale:
            raise SystemExit("%s already holds other TICKLOAD songs %s; use an empty dir" % (songs, stale))
        jobs.append((os.path.join(songs, name), make_cfg(a, **over), desc))
    elif a.output and not a.set:
        jobs.append((a.output, make_cfg(a), "custom"))
    else:
        for name, sub, over, desc in STANDARD_SET:
            d = os.path.join(a.outdir, sub)
            os.makedirs(d, exist_ok=True)
            jobs.append((os.path.join(d, name), make_cfg(a, **over), desc))
        # clean up the earlier VARIANTS/ layout (duplicates there would only confuse)
        old = os.path.join(a.outdir, OLD_VARIANT_SUBDIR)
        if os.path.isdir(old):
            for f in os.listdir(old):
                if "TICKLOAD" in f.upper():
                    os.remove(os.path.join(old, f))
                    print("removed old %s" % os.path.join(old, f))
            if not os.listdir(old):
                os.rmdir(old)
    ok = True
    for path, cfg, desc in jobs:
        if cfg.osc2 == "off":
            cfg.osc2 = "square"  # type kept, level muted in soundParams (oscBVolume = 0x80000000)
            cfg.osc2_muted = True
        xml = build_song(cfg)
        if os.path.exists(path):
            os.remove(path)
        with open(path, "w", newline="\n") as f:
            f.write(xml)
        print("wrote %s (%d bytes) - %s" % (path, len(xml), desc))
        ok = check_song(path, table=a.verbose) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
