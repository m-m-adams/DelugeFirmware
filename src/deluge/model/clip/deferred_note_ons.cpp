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

#include "model/clip/deferred_note_ons.h"
#include "definitions_cxx.hpp"
#include "model/clip/instrument_clip.h"
#include "model/instrument/kit.h"
#include "model/model_stack.h"
#include "model/note/note_row.h"
#include "model/song/song.h"
#include "modulation/arpeggiator.h"
#include "playback/playback_handler.h"
#include "processing/engines/audio_engine.h"
#include "processing/sound/sound_drum.h"
#include "processing/sound/sound_instrument.h"
#include <algorithm>
#include <array>

namespace DeferredNoteOns {
namespace {

struct DeferredNoteOn {
	InstrumentClip* clip;
	Output* output;   // The Clip's Output when the note was queued - it must still be when it's sent
	NoteRow* noteRow; // Only compared with the Clip's NoteRows, never followed until it's found among them
	Drum* drum;       // For a kit, the NoteRow's Drum when the note was queued
	int32_t noteRowId;
	uint32_t sampleSyncLength;
	int32_t ticksLate;
	int64_t swungTickQueued; // playbackHandler.lastSwungTickActioned when queued, which ticksLate is relative to
	uint8_t velocity;
};

// A step of a busy song can hold back a few dozen notes; when this is full, notes are just sent as before
constexpr int32_t kMaxDeferred = 64;

// Oldest first
std::array<DeferredNoteOn, kMaxDeferred> queue;
int32_t numDeferred = 0;

void removeAt(int32_t i) {
	std::move(queue.begin() + i + 1, queue.begin() + numDeferred, queue.begin() + i);
	numDeferred--;
}

template <typename Pred>
void removeIf(Pred pred) {
	auto end = std::remove_if(queue.begin(), queue.begin() + numDeferred, pred);
	numDeferred = static_cast<int32_t>(end - queue.begin());
}

/// Whether a note-on for this NoteRow just starts a voice on a Sound, with nothing (arpeggiator, mono / legato voice
/// handling, choke) that depends on exactly when it's sent relative to the tick's other notes
bool soundJustPlaysNote(InstrumentClip* clip, NoteRow* noteRow) {
	Output* output = clip->output;
	if (output == nullptr || !output->inValidState || clip->arpSettings.mode != ArpMode::OFF) {
		return false;
	}

	if (output->type == OutputType::SYNTH) {
		return static_cast<SoundInstrument*>(output)->polyphonic == PolyphonyMode::POLY;
	}

	if (output->type == OutputType::KIT) {
		Drum* drum = noteRow->drum;
		if (drum == nullptr || drum->type != DrumType::SOUND || drum->arpSettings.mode != ArpMode::OFF) {
			return false;
		}
		// Choke drums choke the others at note-on, whether or not they get a voice, so they stay in the tick
		return static_cast<SoundDrum*>(drum)->polyphonic != PolyphonyMode::CHOKE;
	}

	return false;
}

bool clipIsInCurrentSong(InstrumentClip* clip) {
	return currentSong != nullptr
	       && (currentSong->sessionClips.getIndexForClip(clip) >= 0
	           || currentSong->arrangementOnlyClips.getIndexForClip(clip) >= 0);
}

void send(DeferredNoteOn const& deferred) {
	InstrumentClip* clip = deferred.clip;

	// Everything about the note must be as it was. Anything that stops a note while it's queued (the NoteRow's note
	// ending, the Clip stopping, muting, playback stopping, the Clip being deleted) takes it out of the queue, so
	// these only catch edits made in the few ms it's queued for
	if (!clipIsInCurrentSong(clip) || clip->output != deferred.output || clip->output->getActiveClip() != clip) {
		return;
	}
	int32_t noteRowIndex = -1;
	for (int32_t i = 0; i < clip->noteRows.getNumElements(); i++) {
		if (clip->noteRows.getElement(i) == deferred.noteRow) {
			noteRowIndex = i;
			break;
		}
	}
	if (noteRowIndex < 0) {
		return;
	}
	NoteRow* noteRow = deferred.noteRow;
	int32_t noteRowId = clip->getNoteRowId(noteRow, noteRowIndex);
	if (noteRow->muted || noteRow->drum != deferred.drum || noteRowId != deferred.noteRowId
	    || !soundJustPlaysNote(clip, noteRow)) {
		return;
	}

	PendingNoteOn noteOn{};
	noteOn.noteRow = noteRow;
	noteOn.noteRowId = noteRowId;
	noteOn.sampleSyncLength = deferred.sampleSyncLength;
	noteOn.velocity = deferred.velocity;
	// Still relative to the tick it was due on, for time-stretched samples' sync
	int64_t swungTicksSince = playbackHandler.lastSwungTickActioned - deferred.swungTickQueued;
	noteOn.ticksLate = deferred.ticksLate + static_cast<int32_t>(std::max<int64_t>(swungTicksSince, 0));

	char modelStackMemory[MODEL_STACK_MAX_SIZE];
	ModelStackWithTimelineCounter* modelStack = setupModelStackWithTimelineCounter(modelStackMemory, currentSong, clip);
	clip->sendPendingNoteOn(modelStack, &noteOn);
}

} // namespace

bool tryDefer(InstrumentClip* clip, PendingNoteOn const& note) {
	if (!AudioEngine::wouldRefuseVoiceStart() || numDeferred >= kMaxDeferred
	    || !soundJustPlaysNote(clip, note.noteRow)) {
		return false;
	}

	queue[numDeferred++] = DeferredNoteOn{
	    .clip = clip,
	    .output = clip->output,
	    .noteRow = note.noteRow,
	    .drum = note.noteRow->drum,
	    .noteRowId = note.noteRowId,
	    .sampleSyncLength = note.sampleSyncLength,
	    .ticksLate = note.ticksLate,
	    .swungTickQueued = playbackHandler.lastSwungTickActioned,
	    .velocity = note.velocity,
	};
	return true;
}

void sendWhatCanStart() {
	if (numDeferred == 0) {
		return;
	}
	if (!playbackHandler.isEitherClockActive()) {
		forgetAll();
		return;
	}
	while (numDeferred > 0 && !AudioEngine::wouldRefuseVoiceStart()) {
		// Take it out first: sending it can stop other notes (and so call forgetNoteRow())
		DeferredNoteOn deferred = queue[0];
		removeAt(0);
		send(deferred);
	}
}

void forgetNoteRow(NoteRow const* noteRow) {
	if (numDeferred == 0) {
		return;
	}
	removeIf([&](DeferredNoteOn const& d) { return d.noteRow == noteRow; });
}

void forgetClip(InstrumentClip const* clip) {
	if (numDeferred == 0) {
		return;
	}
	removeIf([&](DeferredNoteOn const& d) { return d.clip == clip; });
}

void forgetAll() {
	numDeferred = 0;
}

int32_t numQueued() {
	return numDeferred;
}

} // namespace DeferredNoteOns
