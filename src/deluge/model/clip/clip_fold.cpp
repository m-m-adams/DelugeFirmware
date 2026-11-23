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

#include "model/clip/clip_fold.h"
#include "definitions_cxx.hpp"
#include "model/clip/instrument_clip.h"
#include "model/note/note_row.h"
#include "model/output.h"
#include "model/song/song.h"
#include <algorithm>

// In a kit, a row's "yNote" is its index, as everywhere else in InstrumentClip.

namespace {
bool isKit(InstrumentClip& clip) {
	return clip.output->type == OutputType::KIT;
}
} // namespace

bool ClipFold::fold(InstrumentClip& clip, Song* song) {
	if (active_) {
		return true;
	}
	int32_t numFolded = 0;
	int32_t numFoldedBelowScreen = 0;
	for (int32_t i = 0; i < clip.noteRows.getNumElements(); i++) {
		NoteRow* row = clip.noteRows.getElement(i);
		row->inFold = !row->hasNoNotes();
		if (row->inFold) {
			numFolded++;
			if (clip.getYDisplayFromYNote(isKit(clip) ? i : row->y, song) < 0) {
				numFoldedBelowScreen++;
			}
		}
	}
	if (numFolded == 0) {
		return false;
	}

	active_ = true;
	if (numFolded <= kDisplayHeight) {
		// They all fit: centre them
		scroll_ = -(kDisplayHeight - numFolded) / 2;
	}
	else {
		// Keep the lowest row that was on screen at the bottom
		scroll_ = std::clamp<int32_t>(numFoldedBelowScreen, 0, numFolded - kDisplayHeight);
	}
	return true;
}

void ClipFold::unfold(InstrumentClip& clip, Song* song, int32_t keepYDisplay) {
	if (!active_) {
		return;
	}
	int32_t numFolded = numFoldedRows(clip);
	if (numFolded == 0) {
		// The folded rows have all been deleted. Leave yScroll where it was.
		active_ = false;
		scroll_ = 0;
		return;
	}

	int32_t yDisplay = keepYDisplay;
	int32_t foldIndex = keepYDisplay + scroll_;
	bool keepIsRow = (keepYDisplay >= 0 && keepYDisplay < kDisplayHeight)
	                 && (!isKit(clip) || (foldIndex >= 0 && foldIndex < numFolded)); // Past the fold, a kit has no rows
	if (!keepIsRow) {
		yDisplay = std::clamp<int32_t>(scroll_, 0, numFolded - 1) - scroll_;
	}
	// An accidental goes back to being shown on its anchor's row
	int32_t yNote = clip.getDisplayAnchorYNote(clip.getYNoteFromYDisplay(yDisplay, song), song);

	active_ = false;
	scroll_ = 0;
	clip.yScroll += clip.getYDisplayFromYNote(yNote, song) - yDisplay;
}

bool ClipFold::addRowsThatGainedNotes(InstrumentClip& clip) {
	if (!active_) {
		return false;
	}
	bool added = false;
	int32_t numFoldedBelow = 0;
	for (int32_t i = 0; i < clip.noteRows.getNumElements(); i++) {
		NoteRow* row = clip.noteRows.getElement(i);
		if (!row->inFold && !row->hasNoNotes()) {
			row->inFold = true;
			added = true;
			// A row added below the screen pushes the rows on it up: follow them. One added below the lowest folded
			// row while that's above the bottom of the screen goes into the gap, so the rows on screen stay put too.
			if (numFoldedBelow < scroll_ || (numFoldedBelow == 0 && scroll_ < 0)) {
				scroll_++;
			}
		}
		numFoldedBelow += row->inFold;
	}
	return added;
}

void ClipFold::refold(InstrumentClip& clip) {
	if (!active_) {
		return;
	}
	int32_t numFolded = 0;
	for (int32_t i = 0; i < clip.noteRows.getNumElements(); i++) {
		NoteRow* row = clip.noteRows.getElement(i);
		row->inFold = !row->hasNoNotes();
		numFolded += row->inFold;
	}
	if (numFolded == 0) {
		// Nothing to fold. yScroll has been kept up to date by whatever moved the notes.
		active_ = false;
		scroll_ = 0;
		return;
	}
	scroll_ = std::clamp<int32_t>(scroll_, 1 - kDisplayHeight, numFolded - 1);
}

bool ClipFold::mayScrollBy(InstrumentClip& clip, int32_t amount) const {
	int32_t newScroll = scroll_ + amount;
	return newScroll >= 1 - kDisplayHeight && newScroll <= numFoldedRows(clip) - 1;
}

int32_t ClipFold::getYNote(InstrumentClip& clip, Song* song, int32_t yDisplay) const {
	int32_t wanted = yDisplay + scroll_; // Index among the folded rows
	int32_t numRows = clip.noteRows.getNumElements();
	int32_t numFolded = 0;
	NoteRow* lowest = nullptr;
	NoteRow* highest = nullptr;
	for (int32_t i = 0; i < numRows; i++) {
		NoteRow* row = clip.noteRows.getElement(i);
		if (!row->inFold) {
			continue;
		}
		if (numFolded == wanted) {
			return isKit(clip) ? i : row->y;
		}
		lowest = lowest ? lowest : row;
		highest = row;
		numFolded++;
	}

	// Past an end of the fold
	if (isKit(clip)) {
		return (wanted < 0) ? wanted : numRows + wanted - numFolded; // Never a real row
	}
	if (!lowest) {
		return clip.getYNoteFromYVisual(yDisplay + clip.yScroll, song); // Nothing folded
	}
	// Carry on from the end row in the clip's own row spacing (scale degrees or semitones). An accidental end row
	// counts as the in-scale row below it, which still puts the next row strictly beyond it.
	int32_t rowsBeyond = (wanted < 0) ? wanted : wanted - (numFolded - 1);
	int32_t edgeYNote = (wanted < 0) ? lowest->y : highest->y;
	return song->getYNoteFromYVisual(song->getYVisualFromYNote(edgeYNote, clip.inScaleMode) + rowsBeyond,
	                                 clip.inScaleMode);
}

int32_t ClipFold::getYDisplay(InstrumentClip& clip, Song* song, int32_t yNote) const {
	int32_t numFolded = 0;
	int32_t numFoldedBelow = 0;
	bool isFolded = false;
	NoteRow* lowest = nullptr;
	NoteRow* highest = nullptr;
	for (int32_t i = 0; i < clip.noteRows.getNumElements(); i++) {
		NoteRow* row = clip.noteRows.getElement(i);
		if (!row->inFold) {
			continue;
		}
		int32_t rowYNote = isKit(clip) ? i : row->y;
		if (rowYNote < yNote) {
			numFoldedBelow++;
		}
		else if (rowYNote == yNote) {
			isFolded = true;
		}
		lowest = lowest ? lowest : row;
		highest = row;
		numFolded++;
	}

	if (isFolded) {
		return numFoldedBelow - scroll_;
	}
	if (isKit(clip)) {
		return kNotShown;
	}
	if (!lowest) {
		return clip.getYVisualFromYNote(yNote, song) - clip.yScroll; // Nothing folded
	}
	auto yVisual = [&](int32_t y) { return song->getYVisualFromYNote(y, clip.inScaleMode); };
	int32_t index;
	if (numFoldedBelow == 0) {
		// Below the fold, on the rows getYNote() makes there
		index = std::min<int32_t>(yVisual(yNote) - yVisual(lowest->y), -1);
	}
	else if (numFoldedBelow == numFolded) {
		// Above the fold
		index = numFolded - 1 + std::max<int32_t>(yVisual(yNote) - yVisual(highest->y), 1);
	}
	else {
		// Between two folded rows: shown on the one below
		index = numFoldedBelow - 1;
	}
	return index - scroll_;
}

int32_t ClipFold::numFoldedRows(InstrumentClip& clip) {
	int32_t numFolded = 0;
	for (int32_t i = 0; i < clip.noteRows.getNumElements(); i++) {
		numFolded += clip.noteRows.getElement(i)->inFold;
	}
	return numFolded;
}
