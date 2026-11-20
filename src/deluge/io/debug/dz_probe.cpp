/*
 * Copyright © 2026 Synthstrom Audible Limited
 *
 * This file is part of The Synthstrom Audible Deluge Firmware.
 *
 * The Synthstrom Audible Deluge Firmware is free software: you can redistribute it and/or modify it under the
 * terms of the GNU General Public License as published by the Free Software Foundation,
 * either version 3 of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
 * without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 * See the GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along with this program.
 * If not, see <https://www.gnu.org/licenses/>.
 */

#include "io/debug/dz_probe.h"

#if DZ_PROBE

#include "definitions.h"
#include "definitions_cxx.hpp"
#include "fatfs/ff.h"
#include "gui/ui/load/load_song_ui.h"
#include "gui/ui/ui.h"
#include "model/song/song.h"
#include "playback/playback_handler.h"
#include "processing/engines/audio_engine.h"
#include "scheduler_api.h"
#include "storage/storage_manager.h"
#include "util/d_string.h"
#include <algorithm>
#include <cstddef>
#include <cstring>
#include <strings.h>

// Layout must match tools/perf/dzprobe.py (FIELDS, then the TICK_FIELDS this probe appends)
struct DzWindow {
	// _BASE
	uint32_t start; // audioSampleTimer at the window's first render
	uint32_t renders;
	uint32_t maxGap; // most PMU cycles from the end of one render to the start of the next
	uint32_t maxGapCaller;
	uint32_t maxGapTask;
	uint32_t maxGapTaskTime;
	uint32_t maxRender; // most PMU cycles one render (routine_() call that rendered, song tick included) took
	uint32_t maxRenderSamples;
	uint32_t maxRenderVoicesStarted;
	uint32_t maxRenderHops;
	uint32_t maxTask;
	uint32_t maxTaskTime;
	uint32_t maxIrq;
	uint32_t irqs;
	uint32_t culls; // voices (or audio clips) culled
	uint32_t late;
	uint32_t clustersFinished;
	uint32_t frames; // samples rendered
	// sum_ then max_ of cyc ins l1i l1d l1dacc istall dstall l2rd l2rdhit, over renders
	uint32_t sum[9];
	uint32_t max[9];
	uint32_t maxFrames;
	uint32_t voices;      // synth voices at the window's end
	uint32_t audioVoices; // audio clip voices at the window's end
	uint32_t scopeCalls[6];
	uint32_t scopeIns[6];
	uint32_t scopeCyc[6];
	uint32_t delayRateChange;
	uint32_t delaySecondary;
	uint32_t delayResampling;
	uint32_t delayAnalog;
	uint32_t tickInvMin;
	uint32_t tickInvMax;
	// TICK_FIELDS
	uint32_t playing;          // renders while playback was on
	uint32_t ticks;            // timer and swung ticks actioned by tickSongFinalizeWindows()
	uint32_t tickRenders;      // renders with at least one tick
	uint32_t sumTickCyc;       // PMU cycles actioning ticks
	uint32_t maxTickCyc;       // most in one render
	uint32_t sumRenderCycTick; // render cycles (after the tick: renderAudio() and what follows) of renders with a tick
	uint32_t maxRenderCycTick;
	uint32_t sumRenderCycNoTick; // ... and of renders without
	uint32_t maxRenderCycNoTick;
	uint32_t startedTick;    // voices started by Sound::noteOn() during a tick
	uint32_t startedOther;   // ... outside a tick (MIDI, auditioning)
	uint32_t startedPending; // pending notes started by process_postarp_notes() in a render
	uint32_t refusedTick;    // allowedToStartVoice() said no in Sound::noteOn() during a tick (note left pending)
	uint32_t refusedOther;
	uint32_t refusedPending; // ... in process_postarp_notes() (a pending note stays pending)
	uint32_t maxPending;     // most notes estimated pending (left pending minus pending started)
	uint32_t cullKill;
	uint32_t cullTerminate;
	uint32_t cullForceRelease;
	uint32_t cullEventsHard;
	uint32_t cullEventsSoft;
	uint32_t cullEventsUnderMin;
	// culls, and renders, by renders since the last render with a tick: 0 (culled at the start of a render which then
	// had a tick), 1 (the render after a tick), 2, 3-4, 5-8, more
	uint32_t cullsAt[6];
	uint32_t rendersAt[6];
	uint32_t maxDireness;
	uint32_t minQueued; // least of what was still queued for the codec at the start of a render, samples
	int32_t minMargin;  // least predicted margin in setDireness(), samples
	uint32_t voiceSum;  // synth voices summed over renders (average = voiceSum / renders)
	uint32_t maxVoices;
	uint32_t maxSongTickCost; // most songTickCost.get() seen, samples * 16
};

constexpr uint32_t kNumWindows = 2048;
constexpr uint32_t kWindowSamples = 4410; // 100 ms
constexpr uint32_t kNumFields = sizeof(DzWindow) / 4;
constexpr uint32_t kVersion = 0x00020001; // 2 = tick probe

struct DzProbe {
	uint32_t magic;
	uint32_t index; // window being filled; windows before it are complete
	DzWindow windows[kNumWindows];
	uint32_t numFields;
	uint32_t version;
	uint32_t playIndex; // window index when play was pressed (dzPlayIndex)
};
static_assert(offsetof(DzWindow, playing) == 4 * 63); // the end of dzprobe.py's FIELDS
static_assert(sizeof(DzWindow) == 4 * (63 + 40));
static_assert(sizeof(DzProbe) == 8 + kNumWindows * sizeof(DzWindow) + 12);

// One per render, see tools/dzprobe.py RENDER_FIELDS
struct DzRender {
	uint32_t timer;     // audioSampleTimer at the start of the render
	uint32_t cycStart;  // PMU cycle counter at the top of routine_()
	uint32_t totalCyc;  // whole routine_() call
	uint32_t tickCyc;   // actioning ticks
	uint32_t renderCyc; // renderAudio() and what follows
	uint16_t space;     // space in the output buffer at the start (128 - what's queued)
	uint16_t samples;   // rendered
	uint8_t ticks;
	uint8_t startedTick;
	uint8_t startedPending;
	uint8_t culls;
	uint16_t voices;
	uint8_t direness;
	uint8_t flags;     // see kFlag*
	int16_t margin;    // predicted margin in setDireness(), samples
	uint16_t tickCost; // songTickCost.get() in setDireness(), samples * 16
	uint16_t refused;  // allowedToStartVoice() refusals
	uint16_t pending;  // estimated pending notes at the end
};
static_assert(sizeof(DzRender) == 40);

constexpr uint32_t kNumRenders = 16384;
constexpr uint8_t kFlagPlaying = 1 << 0;
constexpr uint8_t kFlagCullHard = 1 << 1;
constexpr uint8_t kFlagCullSoft = 1 << 2;
constexpr uint8_t kFlagCullUnderMin = 1 << 3;
constexpr uint8_t kFlagSecondRender = 1 << 4; // second routine_() of one AudioEngine::routine() call
constexpr uint8_t kFlagDireness = 1 << 5;     // setDireness() reported (margin valid)

struct DzRenders {
	uint32_t magic; // 0xD2D2D2D3
	uint32_t index; // renders recorded; entry i is at i % kNumRenders
	uint32_t entrySize;
	uint32_t numEntries;
	DzRender entries[kNumRenders];
};

extern "C" {
PLACE_SDRAM_BSS DzProbe dzProbe;
PLACE_SDRAM_BSS DzRenders dzRenders;
// File-name substring of the song under SONGS/ to load and play at boot. Patch it in the ELF or change it here
char dzSongPath[64] = "TICKLOAD";
// 1: load dzSongPath and press play at boot; 0: don't
volatile uint32_t dzCommand = 1;
// 0 off, 1 waiting for the card, 2 looking for the song, 3 loading, 4 loaded, 5 playing; 0xE1 no SONGS folder, 0xE2 no
// matching song, 0xE3 load UI wouldn't open
volatile uint32_t dzAutoStage = 0;
volatile int32_t dzResult = 0;
volatile uint32_t dzLoadIndex = 0;
volatile uint32_t dzPlayIndex = 0;
char dzLoadedSong[64];
}

namespace dz {
namespace {

constexpr uint32_t kNumEvents = 6;
// Cortex-A9 events into sum[1..6]: instructions renamed (the A9's instruction count), L1I refill, L1D refill, L1D
// access, I-cache dependent stall cycles, D-cache dependent stall cycles. QEMU counts none of them (reads 0)
constexpr uint32_t kEvents[kNumEvents] = {0x68, 0x01, 0x03, 0x04, 0x60, 0x61};
uint32_t numEvents = 0;

// Per render
uint32_t rStart, rRenderStart, rTickStart, rTickCyc, lastEnd;
uint32_t rEvents[kNumEvents];
uint32_t rSpace;
uint32_t rTimer;
uint8_t rTicks, rStartedTick, rStartedPending, rStartedOther, rCulls, rFlags;
uint16_t rRefused;
int32_t rMargin;
uint32_t rTickCost;
bool inTick = false;
uint32_t rendersSinceTick = 1000;
int32_t pending = 0;
bool started = false;

inline DzWindow& win() {
	return dzProbe.windows[dzProbe.index % kNumWindows];
}

void startWindow(DzWindow& w, uint32_t timer) {
	memset(&w, 0, sizeof(w));
	w.start = timer;
	w.minQueued = 0xFFFFFFFF;
	w.minMargin = INT32_MAX;
	w.tickInvMin = 0;
}

inline uint8_t sat8(uint32_t v) {
	return v > 255 ? 255 : v;
}

inline uint32_t readEvent(uint32_t i) {
	uint32_t c;
	asm volatile("MCR p15, 0, %1, c9, c12, 5\n"
	             "MRC p15, 0, %0, c9, c13, 2"
	             : "=r"(c)
	             : "r"(i));
	return c;
}

inline void bump(uint8_t& v) {
	if (v < 255) {
		v++;
	}
}

inline uint32_t bucket(uint32_t rendersSince) {
	if (rendersSince <= 2) {
		return rendersSince;
	}
	if (rendersSince <= 4) {
		return 3;
	}
	if (rendersSince <= 8) {
		return 4;
	}
	return 5;
}

// Autoplay

bool isXml(const char* name) {
	size_t n = strlen(name);
	return n > 4 && !strcasecmp(name + n - 4, ".XML");
}

bool containsNoCase(const char* haystack, const char* needle) {
	size_t n = strlen(needle);
	for (; *haystack; haystack++) {
		if (!strncasecmp(haystack, needle, n)) {
			return true;
		}
	}
	return n == 0;
}

void autoPlay() {
	if (!playbackHandler.isEitherClockActive()) {
		playbackHandler.playButtonPressed(kInternalButtonPressLatency);
	}
	dzPlayIndex = dzProbe.index;
	dzProbe.playIndex = dzProbe.index;
	dzAutoStage = 5;
}

void autoLoad() {
	dzAutoStage = 2;
	DIR dir;
	if (f_opendir(&dir, "SONGS") != FR_OK) {
		dzAutoStage = 0xE1;
		return;
	}
	// The alphabetically first match, so it doesn't depend on directory order
	static char best[256];
	best[0] = 0;
	static FILINFO fno;
	while (f_readdir(&dir, &fno) == FR_OK && fno.fname[0] != 0) {
		if ((fno.fattrib & AM_DIR) || !isXml(fno.fname) || !containsNoCase(fno.fname, dzSongPath)) {
			continue;
		}
		if (!best[0] || strcasecmp(fno.fname, best) < 0) {
			strncpy(best, fno.fname, sizeof(best) - 1);
		}
	}
	f_closedir(&dir);
	if (!best[0]) {
		dzAutoStage = 0xE2;
		return;
	}
	strncpy(dzLoadedSong, best, sizeof(dzLoadedSong) - 1);

	// The way setupStartupSong() loads a song
	String path;
	path.set("SONGS/");
	path.concatenate(best);
	dzLoadIndex = dzProbe.index;
	dzAutoStage = 3;
	currentSong->setSongFullPath(path.get());
	if (!openUI(&loadSongUI)) {
		dzAutoStage = 0xE3;
		return;
	}
	loadSongUI.performLoad();
	dzAutoStage = 4;
	// Press play once the song's view is up and its samples have had a moment to start loading
	addOnceTask(autoPlay, 101, 1.0, "dz autoplay", RESOURCE_NONE);
}

} // namespace

void init() {
	// PMU: cycle counter plus up to six event counters, no divider, no overflow interrupts
	uint32_t pmcr;
	asm volatile("MRC p15, 0, %0, c9, c12, 0" : "=r"(pmcr));
	numEvents = std::min<uint32_t>((pmcr >> 11) & 0x1f, kNumEvents);
	for (uint32_t i = 0; i < numEvents; i++) {
		asm volatile("MCR p15, 0, %0, c9, c12, 5\n"
		             "MCR p15, 0, %1, c9, c13, 1" ::"r"(i),
		             "r"(kEvents[i]));
	}
	asm volatile("MCR p15, 0, %0, c9, c14, 2" ::"r"(0xFFFFFFFFu));                           // PMINTENCLR
	asm volatile("MCR p15, 0, %0, c9, c12, 1" ::"r"(0x80000000u | ((1u << numEvents) - 1))); // PMCNTENSET
	pmcr = (pmcr & ~(1u << 3)) | 0b111;                                                      // E, P, C; D off
	asm volatile("MCR p15, 0, %0, c9, c12, 0" ::"r"(pmcr));

	memset(&dzProbe, 0, sizeof(dzProbe));
	dzProbe.numFields = kNumFields;
	dzProbe.version = kVersion;
	startWindow(dzProbe.windows[0], 0);
	dzProbe.magic = 0xD2D2D2D2;

	memset(&dzRenders, 0, sizeof(dzRenders));
	dzRenders.entrySize = sizeof(DzRender);
	dzRenders.numEntries = kNumRenders;
	dzRenders.magic = 0xD2D2D2D3;
}

void registerAutoplay() {
	if (dzCommand != 1) {
		return;
	}
	dzAutoStage = 1;
	// After setupStartupSong() (priority 100), which also waits for the card
	addConditionalTask(autoLoad, 101, isCardReady, "dz autoload", RESOURCE_SD | RESOURCE_SD_ROUTINE);
}

void routineBegin() {
	rStart = cycles();
}

void renderBegin(uint32_t spaceInBuffer, uint32_t numRoutinesSoFar) {
	for (uint32_t i = 0; i < numEvents; i++) {
		rEvents[i] = readEvent(i);
	}
	rSpace = spaceInBuffer;
	rTimer = AudioEngine::audioSampleTimer;
	rTicks = rStartedTick = rStartedPending = rStartedOther = rCulls = 0;
	rRefused = 0;
	rTickCyc = 0;
	rMargin = 0;
	rTickCost = 0;
	rFlags = numRoutinesSoFar ? kFlagSecondRender : 0;
	rRenderStart = rStart;

	if (!started) {
		started = true;
		dzProbe.windows[dzProbe.index % kNumWindows].start = rTimer;
	}
	else {
		DzWindow& w = win();
		w.maxGap = std::max(w.maxGap, rStart - lastEnd);
	}
	DzWindow& w = win();
	w.minQueued = std::min<uint32_t>(w.minQueued, SSI_TX_BUFFER_NUM_SAMPLES - spaceInBuffer);
}

void direness(int32_t margin, float songTickCostSamples) {
	rMargin = margin;
	rTickCost = (uint32_t)(songTickCostSamples * 16);
	rFlags |= kFlagDireness;
	DzWindow& w = win();
	w.minMargin = std::min(w.minMargin, margin);
	w.maxSongTickCost = std::max(w.maxSongTickCost, rTickCost);
}

void cullEvent(CullEvent kind) {
	DzWindow& w = win();
	switch (kind) {
	case CullEvent::HARD:
		w.cullEventsHard++;
		rFlags |= kFlagCullHard;
		break;
	case CullEvent::SOFT:
		w.cullEventsSoft++;
		rFlags |= kFlagCullSoft;
		break;
	case CullEvent::UNDER_MIN:
		w.cullEventsUnderMin++;
		rFlags |= kFlagCullUnderMin;
		break;
	}
}

void culled(Cull kind) {
	DzWindow& w = win();
	w.culls++;
	switch (kind) {
	case Cull::KILL:
		w.cullKill++;
		break;
	case Cull::TERMINATE:
		w.cullTerminate++;
		break;
	case Cull::FORCE_RELEASE:
		w.cullForceRelease++;
		break;
	}
	bump(rCulls);
}

void tickBegin() {
	inTick = true;
	rTickStart = cycles();
}

void tickEnd() {
	rTickCyc += cycles() - rTickStart;
	inTick = false;
	bump(rTicks);
	win().ticks++;
}

void renderAudioBegin() {
	rRenderStart = cycles();
}

bool dispatchingDeferred = false;

void voiceStartAsked(bool allowed, bool fromPending) {
	// E3: a held-back note-on sent from a render counts as a pending note started (or refused)
	fromPending |= dispatchingDeferred;
	DzWindow& w = win();
	if (fromPending) {
		if (allowed) {
			w.startedPending++;
			bump(rStartedPending);
			if (pending > 0) {
				pending--;
			}
		}
		else {
			w.refusedPending++;
			rRefused++;
		}
		return;
	}
	if (allowed) {
		if (inTick) {
			w.startedTick++;
			bump(rStartedTick);
		}
		else {
			w.startedOther++;
			bump(rStartedOther);
		}
		return;
	}
	if (inTick) {
		w.refusedTick++;
	}
	else {
		w.refusedOther++;
	}
	rRefused++;
	pending++;
	w.maxPending = std::max<uint32_t>(w.maxPending, pending);
}

void noteDeferred() {
	// E3: held back from the tick instead of being refused there - same counters as a refusal
	voiceStartAsked(false, false);
}

void renderEnd(uint32_t numSamples, uint32_t hops, uint32_t timerAfter) {
	uint32_t now = cycles();
	uint32_t total = now - rStart;
	uint32_t renderCyc = now - rRenderStart;
	DzWindow& w = win();

	bool playing = playbackHandler.isEitherClockActive();
	if (playing) {
		w.playing++;
		rFlags |= kFlagPlaying;
	}
	else {
		pending = 0; // Nothing can be pending once playback's stopped (and the estimate can't drift for ever)
	}

	w.renders++;
	w.frames += numSamples;
	w.maxFrames = std::max(w.maxFrames, numSamples);
	w.sum[0] += total;
	w.max[0] = std::max(w.max[0], total);
	for (uint32_t i = 0; i < numEvents; i++) {
		uint32_t d = readEvent(i) - rEvents[i];
		w.sum[1 + i] += d;
		w.max[1 + i] = std::max(w.max[1 + i], d);
	}
	if (total > w.maxRender) {
		w.maxRender = total;
		w.maxRenderSamples = numSamples;
		w.maxRenderVoicesStarted = rStartedTick + rStartedPending + rStartedOther;
		w.maxRenderHops = hops;
	}

	if (rTicks) {
		w.tickRenders++;
		w.sumTickCyc += rTickCyc;
		w.maxTickCyc = std::max(w.maxTickCyc, rTickCyc);
		w.sumRenderCycTick += renderCyc;
		w.maxRenderCycTick = std::max(w.maxRenderCycTick, renderCyc);
	}
	else {
		w.sumRenderCycNoTick += renderCyc;
		w.maxRenderCycNoTick = std::max(w.maxRenderCycNoTick, renderCyc);
	}
	uint32_t b = bucket(rTicks ? 0 : rendersSinceTick);
	w.cullsAt[b] += rCulls;
	w.rendersAt[b]++;
	rendersSinceTick = rTicks ? 1 : std::min<uint32_t>(rendersSinceTick + 1, 1000);

	uint32_t voices = AudioEngine::getNumVoices();
	w.voiceSum += voices;
	w.maxVoices = std::max(w.maxVoices, voices);
	w.maxDireness = std::max<uint32_t>(w.maxDireness, AudioEngine::cpuDireness);

	DzRender& r = dzRenders.entries[dzRenders.index % kNumRenders];
	r.timer = rTimer;
	r.cycStart = rStart;
	r.totalCyc = total;
	r.tickCyc = rTickCyc;
	r.renderCyc = renderCyc;
	r.space = rSpace;
	r.samples = numSamples;
	r.ticks = rTicks;
	r.startedTick = rStartedTick;
	r.startedPending = rStartedPending;
	r.culls = rCulls;
	r.voices = std::min<uint32_t>(voices, UINT16_MAX);
	r.direness = AudioEngine::cpuDireness;
	r.flags = rFlags;
	r.margin = std::clamp<int32_t>(rMargin, INT16_MIN, INT16_MAX);
	r.tickCost = std::min<uint32_t>(rTickCost, UINT16_MAX);
	r.refused = rRefused;
	r.pending = std::min<int32_t>(pending, UINT16_MAX);
	dzRenders.index++;

	if (timerAfter - w.start >= kWindowSamples) {
		w.voices = voices;
		w.audioVoices = currentSong != nullptr ? currentSong->countAudioVoices() : 0;
		dzProbe.index++;
		startWindow(win(), timerAfter);
	}
	lastEnd = cycles();
}

void Scope::end(uint8_t slot, uint32_t cyc, uint32_t ins) {
	DzWindow& w = win();
	w.scopeCalls[slot]++;
	w.scopeIns[slot] += ins;
	w.scopeCyc[slot] += cyc;
}

} // namespace dz

#endif // DZ_PROBE
