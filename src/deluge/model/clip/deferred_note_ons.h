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

#pragma once

#include <cstdint>

class InstrumentClip;
class NoteRow;
struct PendingNoteOn;

/// Sequenced note-ons held back from the song tick.
///
/// Only so many voices may start per render (AudioEngine::allowedToStartVoice()). A note-on the tick sends once that
/// many have started can't get a voice anyway: today it still goes through the whole note-on path (instrument, kit arp,
/// the Sound's arpeggiator bookkeeping, render-skipping checks), is left pending, and a later render starts it from
/// Sound::render(). With 20-30 notes on a step, that bookkeeping is most of the tick's cost, and the tick comes on top
/// of the render it precedes - and songTickCost reserves the most a tick has cost lately in every render's prediction.
///
/// So when the tick wants to send a note-on that would be refused, and the note goes to a Sound that just plays it
/// (polyphonic, arpeggiator off), the note is queued here instead, and each render sends as many queued notes as can
/// start a voice, oldest first, before the song renders. That's the render which would otherwise have started it from
/// pending, so the voice starts no later than it does now, and its note-on work is done once, spread over the renders
/// after the tick rather than all in it.
///
/// Notes for MIDI, CV and gate outputs, arpeggiated Sounds, mono/legato Sounds and choke drums are never held back.
namespace DeferredNoteOns {

/// Called by the tick instead of sending a note-on. Returns whether the note was queued; if not, send it as usual
bool tryDefer(InstrumentClip* clip, PendingNoteOn const& note);

/// Send what can start a voice in this render. Call once per render, after the song tick and before rendering
void sendWhatCanStart();

/// The NoteRow's sequenced note is being stopped: a note-on still queued for it must not start
void forgetNoteRow(NoteRow const* noteRow);

/// The Clip is going away
void forgetClip(InstrumentClip const* clip);

/// Playback stopped
void forgetAll();

/// How many note-ons are queued
int32_t numQueued();

} // namespace DeferredNoteOns
