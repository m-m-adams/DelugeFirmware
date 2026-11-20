"""Shared helpers for the performance tools: firmware symbols, and the dz probe firmware's ring of 100 ms windows.

Local copy of delugemu tools/perf/dzprobe.py for the tick probe (DelugeFirmware branch perf/tick-probe,
src/deluge/io/debug/dz_probe.{h,cpp}). Differences from upstream:
  - TICK_FIELDS appended to FIELDS (song-tick cost, voice starts, cull split, culls by renders since a tick)
  - SCOPE_NAMES / probe_addresses()["scopes"] are tick-path functions instead of DSP
  - a per-render ring, dzRenders (load_renders(), RENDER_FIELDS)
Dumps from the old debug/pmu-render-probe still load (fewer fields).
"""

import struct
import subprocess

NUM_WINDOWS = 2048
_BASE = [
    "start",
    "renders",
    "maxGap",
    "maxGapCaller",
    "maxGapTask",
    "maxGapTaskTime",
    "maxRender",
    "maxRenderSamples",
    "maxRenderVoicesStarted",
    "maxRenderHops",
    "maxTask",
    "maxTaskTime",
    "maxIrq",
    "irqs",
    "culls",
    "late",
    "clustersFinished",
    "frames",
]
_PMU = ["cyc", "ins", "l1i", "l1d", "l1dacc", "istall", "dstall", "l2rd", "l2rdhit"]
_BUCKETS = ["0", "1", "2", "3to4", "5to8", "later"]
# Appended by the tick probe (dz_probe.cpp DzWindow, after tickInvMax). Cycles are PMU cycles (QEMU: 1 GHz of guest
# time; hardware: 400 MHz)
TICK_FIELDS = (
    [
        "playing",
        "ticks",
        "tickRenders",
        "sum_tickCyc",
        "max_tickCyc",
        "sum_renderCycTick",
        "max_renderCycTick",
        "sum_renderCycNoTick",
        "max_renderCycNoTick",
        "startedTick",
        "startedOther",
        "startedPending",
        "refusedTick",
        "refusedOther",
        "refusedPending",
        "maxPending",
        "cullKill",
        "cullTerminate",
        "cullForceRelease",
        "cullEventsHard",
        "cullEventsSoft",
        "cullEventsUnderMin",
    ]
    + ["cullsAt" + b for b in _BUCKETS]
    + ["rendersAt" + b for b in _BUCKETS]
    + [
        "maxDireness",
        "minQueued",
        "minMargin",
        "voiceSum",
        "maxVoices",
        "maxSongTickCost",
    ]
)
SIGNED_FIELDS = {"minMargin"}
# Later probe builds append fields; a dump's size says how many it has
FIELDS = (
    _BASE
    + ["sum_" + p for p in _PMU]
    + ["max_" + p for p in _PMU]
    + ["maxFrames", "voices", "audioVoices"]
    + ["scopeCalls%d" % i for i in range(6)]
    + ["scopeIns%d" % i for i in range(6)]
    + ["scopeCyc%d" % i for i in range(6)]
    + [
        "delayRateChange",
        "delaySecondary",
        "delayResampling",
        "delayAnalog",
        "tickInvMin",
        "tickInvMax",
    ]
    + TICK_FIELDS
)
SCOPE_PREFIXES = [
    "PlaybackHandler::actionSwungTick(",
    "InstrumentClip::processCurrentPos(",
    "NoteRow::processCurrentPos(",
    "Sound::noteOnPostArpeggiator(",
    "Voice::noteOn(",
    "Sound::process_postarp_notes(",
]
SCOPE_NAMES = [p.rstrip("(") for p in SCOPE_PREFIXES]
TICK_HZ = 33.33e6  # getSystemTicks()

# Per-render ring (dzRenders): magic 0xD2D2D2D3, index (renders recorded), entry size, entry count, entries
RENDER_MAGIC = 0xD2D2D2D3
RENDER_FMT = "<IIIIIHHBBBBHBBhHHH"
RENDER_FIELDS = [
    "timer",
    "cycStart",
    "totalCyc",
    "tickCyc",
    "renderCyc",
    "space",
    "samples",
    "ticks",
    "startedTick",
    "startedPending",
    "culls",
    "voices",
    "direness",
    "flags",
    "margin",
    "tickCost",
    "refused",
    "pending",
]
RENDER_FLAGS = {
    "playing": 1,
    "cullHard": 2,
    "cullSoft": 4,
    "cullUnderMin": 8,
    "secondRender": 16,
    "direness": 32,
}


def load_windows(path):
    """Completed windows from a dump of the dzProbe struct, oldest first, each a dict with an "i" index."""
    raw = open(path, "rb").read()
    magic, index = struct.unpack_from("<II", raw)
    if magic != 0xD2D2D2D2:
        raise ValueError("%s: not a dz probe dump (magic %#x)" % (path, magic))
    nfields = (len(raw) - 8 - 12) // NUM_WINDOWS // 4
    names = FIELDS[:nfields]
    size = 4 * nfields
    out = []
    for i in range(max(0, index - NUM_WINDOWS + 1), index):
        w = dict(
            zip(
                names,
                struct.unpack_from("<%dI" % nfields, raw, 8 + (i % NUM_WINDOWS) * size),
            )
        )
        for k in SIGNED_FIELDS & w.keys():
            if w[k] >= 1 << 31:
                w[k] -= 1 << 32
        w["i"] = i
        out.append(w)
    return out


def probe_trailer(path):
    """(numFields, version, playIndex) from the tick probe's last 12 bytes (zeros/garbage for older probes)."""
    raw = open(path, "rb").read()
    return struct.unpack_from("<III", raw, len(raw) - 12)


def load_renders(path):
    """Renders from a dump of dzRenders, oldest first, each a dict with an "n" (render number) and "dt" (cycles from
    the previous render's start; None for the first)."""
    raw = open(path, "rb").read()
    magic, index, esize, n = struct.unpack_from("<IIII", raw)
    if magic != RENDER_MAGIC:
        raise ValueError("%s: not a dzRenders dump (magic %#x)" % (path, magic))
    assert esize == struct.calcsize(RENDER_FMT), esize
    out = []
    prev = None
    for k in range(max(0, index - n), index):
        r = dict(
            zip(
                RENDER_FIELDS, struct.unpack_from(RENDER_FMT, raw, 16 + (k % n) * esize)
            )
        )
        r["n"] = k
        r["dt"] = (r["cycStart"] - prev) & 0xFFFFFFFF if prev is not None else None
        prev = r["cycStart"]
        out.append(r)
    return out


def symbols(elf):
    """{demangled name: (address, size)} for every symbol in the ELF (size 0 for linker-script symbols)."""
    out = {}
    nm = subprocess.run(
        ["arm-none-eabi-nm", "-C", "-S", elf],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    for line in nm.splitlines():
        # "addr size type name", or "addr type name" for symbols without a size
        parts = line.split(" ", 3)
        if len(parts) == 4 and len(parts[2]) == 1:
            out.setdefault(parts[3], (int(parts[0], 16), int(parts[1], 16)))
        elif len(parts) >= 3 and len(parts[1]) == 1:
            out.setdefault(line.split(" ", 2)[2], (int(parts[0], 16), 0))
    return out


def find(syms, prefix):
    """Address of the largest symbol whose name starts with prefix (picks the real body over thunks/clones)."""
    # ")::" would be a function-local static, e.g. Sound::render(...)::sound_memory
    hits = [
        (size, addr)
        for name, (addr, size) in syms.items()
        if name.startswith(prefix) and ")::" not in name
    ]
    if not hits:
        raise KeyError("no symbol starting with %r" % prefix)
    return max(hits)[1]


def probe_addresses(elf):
    """The addresses the plugin and the dump need, resolved from the probe ELF."""
    s = symbols(elf)
    a = {
        "ttb": find(s, "ttb_mmu1_base"),
        "mark": find(s, "PlaybackHandler::playButtonPressed("),
        "render": find(s, "AudioEngine::routine()"),
        "timer": find(s, "AudioEngine::audioSampleTimer"),
        "probe": find(s, "dzProbe"),
        "probe_size": s["dzProbe"][1],
        "scopes": [find(s, p) for p in SCOPE_PREFIXES],
    }
    if "dzRenders" in s:
        a["renders"], a["renders_size"] = s["dzRenders"]
    for k in ("dzSongPath", "dzAutoStage", "dzPlayIndex", "dzLoadIndex"):
        if k in s:
            a[k] = s[k][0]
    return a
