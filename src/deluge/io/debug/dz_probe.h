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

// dz probe: render/tick performance instrumentation for the delugemu perf tools (tools/perf/dzprobe.py). Not for
// merging. Records 100 ms windows (of audioSampleTimer) into dzProbe and every render into dzRenders, both in SDRAM,
// and loads and plays the song named by dzSongPath at boot. With DZ_PROBE 0 every hook compiles to nothing.
#pragma once

#define DZ_PROBE 1

#include <cstdint>

namespace dz {

// Kinds of voice cull (cullVoices() and the functions it calls)
enum class Cull : uint8_t { KILL, TERMINATE, FORCE_RELEASE };
enum class CullEvent : uint8_t { HARD, SOFT, UNDER_MIN };

// DzScope slots: inclusive cycles and calls into the window's scopeCalls/scopeIns/scopeCyc fields
enum ScopeSlot : uint8_t {
	SCOPE_SWUNG_TICK = 0,    // PlaybackHandler::actionSwungTick
	SCOPE_CLIP_POS = 1,      // InstrumentClip::processCurrentPos
	SCOPE_NOTE_ROW_POS = 2,  // NoteRow::processCurrentPos
	SCOPE_NOTE_ON_POST = 3,  // Sound::noteOnPostArpeggiator
	SCOPE_VOICE_NOTE_ON = 4, // Voice::noteOn
	SCOPE_POSTARP = 5,       // pending-note start in Sound::process_postarp_notes (one per pending note tried)
};

#if DZ_PROBE

[[gnu::always_inline]] inline uint32_t cycles() {
	uint32_t c;
	asm volatile("MRC p15, 0, %0, c9, c13, 0" : "=r"(c));
	return c;
}

[[gnu::always_inline]] inline uint32_t instructions() {
	uint32_t c;
	asm volatile("MCR p15, 0, %1, c9, c12, 5\n" // PMSELR = 0
	             "MRC p15, 0, %0, c9, c13, 2"   // PMXEVCNTR
	             : "=r"(c)
	             : "r"(0));
	return c;
}

void init();             ///< PMU on, rings set up. Call early at boot
void registerAutoplay(); ///< Once the card is ready, load the song named by dzSongPath and press play

// Audio routine hooks, in the order they happen in AudioEngine::routine_()
void routineBegin();                                                     ///< top of routine_()
void renderBegin(uint32_t spaceInBuffer, uint32_t numRoutinesSoFar);     ///< routine_() is going to render
void direness(int32_t margin, float songTickCostSamples);                ///< setDireness()
void cullEvent(CullEvent kind);                                          ///< cullVoices() culled
void culled(Cull kind);                                                  ///< a voice (or audio clip) was culled
void tickBegin();                                                        ///< about to action a timer or swung tick
void tickEnd();                                                          ///< done actioning it
void renderAudioBegin();                                                 ///< song tick done, rendering starts
void renderEnd(uint32_t numSamples, uint32_t hops, uint32_t timerAfter); ///< end of routine_()

/// Sound::noteOn() asked to start a voice (fromPending false) or process_postarp_notes() asked to start a pending one
void voiceStartAsked(bool allowed, bool fromPending);
/// E3: a note-on was held back from the tick (DeferredNoteOns); counted like a refusal in the tick
void noteDeferred();
/// E3: true while DeferredNoteOns sends held-back notes, so their voice starts count as pending starts
extern bool dispatchingDeferred;

class Scope {
public:
	[[gnu::always_inline]] explicit Scope(uint8_t slot) : slot_(slot), c0_(cycles()), i0_(instructions()) {}
	[[gnu::always_inline]] ~Scope() { end(slot_, cycles() - c0_, instructions() - i0_); }
	Scope(const Scope&) = delete;
	Scope& operator=(const Scope&) = delete;

private:
	static void end(uint8_t slot, uint32_t cyc, uint32_t ins);
	uint8_t slot_;
	uint32_t c0_;
	uint32_t i0_;
};

#define DZ_CONCAT_(a, b) a##b
#define DZ_CONCAT(a, b) DZ_CONCAT_(a, b)
#define DZ_SCOPE(slot)                                                                                                 \
	::dz::Scope DZ_CONCAT(dzScope_, __LINE__) {                                                                        \
		slot                                                                                                           \
	}
// Keeps a function a real symbol, so the plugin's s0..s5 can count its instructions
#define DZ_NOINLINE [[gnu::noinline]]

#else // DZ_PROBE

inline void init() {
}
inline void registerAutoplay() {
}
inline void routineBegin() {
}
inline void renderBegin(uint32_t, uint32_t) {
}
inline void direness(int32_t, float) {
}
inline void cullEvent(CullEvent) {
}
inline void culled(Cull) {
}
inline void tickBegin() {
}
inline void tickEnd() {
}
inline void renderAudioBegin() {
}
inline void renderEnd(uint32_t, uint32_t, uint32_t) {
}
inline void voiceStartAsked(bool, bool) {
}
inline void noteDeferred() {
}
inline bool dispatchingDeferred = false;
#define DZ_SCOPE(slot)
#define DZ_NOINLINE

#endif // DZ_PROBE

} // namespace dz
