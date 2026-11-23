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
class Song;

/// Folding an InstrumentClip makes the clip editor show only the rows that have notes, packed together with no gaps.
///
/// The folded rows are the NoteRows marked NoteRow::inFold. The mark lives on the row so that it follows the row as
/// rows are inserted, deleted, reordered or change pitch. A row that is emptied keeps its mark until the clip is
/// folded again, so rows don't move under the user's fingers. A row that gains notes (recording, or a press past the
/// ends of a melodic clip's fold) is marked by addRowsThatGainedNotes(). Code that tidies away empty rows (e.g. when
/// saving) can delete an emptied folded row, which just leaves the fold.
///
/// While folded, scroll() takes the place of InstrumentClip::yScroll for the clip editor, and yScroll is left alone.
/// So everything else (key changes, undo, saving, the keyboard screen) carries on using yScroll as before, and
/// unfolding just works out a new yScroll. Folding isn't saved.
///
/// Past the ends of the fold, a melodic clip shows the next pitches on in its usual row spacing, so notes can be added
/// there. A kit has no rows there.
class ClipFold {
public:
	/// What getYDisplay() gives for a kit row that isn't folded
	static constexpr int32_t kNotShown = -10000;

	[[nodiscard]] bool isActive() const { return active_; }
	/// The index among the folded rows of the bottom row on screen. May be negative, or past the last folded row.
	[[nodiscard]] int32_t scroll() const { return scroll_; }

	/// Folds to the rows with notes, keeping the rows on screen roughly where they were. Returns false, changing
	/// nothing, if there are none.
	bool fold(InstrumentClip& clip, Song* song);
	/// Sets clip.yScroll to keep the row at keepYDisplay where it is, if that's on screen and a row the clip has when
	/// unfolded. Otherwise, the lowest folded row on screen (or the nearest one).
	void unfold(InstrumentClip& clip, Song* song, int32_t keepYDisplay = -1);
	/// Folds rows that have gained notes, keeping the rows on screen where they are if it can. Returns true if any
	/// were added.
	bool addRowsThatGainedNotes(InstrumentClip& clip);
	/// After rows have moved and merged (scale changes): folds exactly the rows with notes, keeping scroll(). Unfolds
	/// if there are none.
	void refold(InstrumentClip& clip);

	/// Whether scrolling by amount still leaves a folded row on screen
	[[nodiscard]] bool mayScrollBy(InstrumentClip& clip, int32_t amount) const;
	void scrollBy(int32_t amount) { scroll_ += amount; }

	/// While folded, InstrumentClip::getYNoteFromYDisplay()
	[[nodiscard]] int32_t getYNote(InstrumentClip& clip, Song* song, int32_t yDisplay) const;
	/// While folded, InstrumentClip::getYDisplayFromYNote()
	[[nodiscard]] int32_t getYDisplay(InstrumentClip& clip, Song* song, int32_t yNote) const;

private:
	static int32_t numFoldedRows(InstrumentClip& clip);

	bool active_ = false;
	int32_t scroll_ = 0;
};
