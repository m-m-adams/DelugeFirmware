// E2 behaviour check (not for merging): logs every voice start and post-arp note-off of a Sound, so two firmware
// builds can be compared note by note. Ring in SDRAM, dumped by the emulator (QMP memsave of dzNoteLog).
#include "io/debug/dz_notelog.h"
#include "definitions.h"
#include "processing/engines/audio_engine.h"
#include <cstring>

namespace {
constexpr uint32_t kNumNoteLogEntries = 65536;
struct DzNoteLogEntry {
	uint32_t timer;
	uint32_t nameHash;
	int16_t noteCode;
	uint8_t velocity;
	uint8_t kind;
};
struct DzNoteLog {
	uint32_t magic;
	uint32_t index;
	uint32_t entrySize;
	uint32_t numEntries;
	DzNoteLogEntry entries[kNumNoteLogEntries];
};
} // namespace

extern "C" {
PLACE_SDRAM_BSS DzNoteLog dzNoteLog;
}

namespace dz {
void noteLog(char const* name, int32_t noteCode, int32_t velocity, uint8_t kind) {
	if (dzNoteLog.magic != 0xD2D2D2D4) {
		memset(&dzNoteLog, 0, sizeof(dzNoteLog));
		dzNoteLog.entrySize = sizeof(DzNoteLogEntry);
		dzNoteLog.numEntries = kNumNoteLogEntries;
		dzNoteLog.magic = 0xD2D2D2D4;
	}
	uint32_t h = 2166136261u;
	for (char const* c = name; c && *c; c++) {
		h = (h ^ (uint8_t)*c) * 16777619u;
	}
	DzNoteLogEntry& e = dzNoteLog.entries[dzNoteLog.index % kNumNoteLogEntries];
	e.timer = AudioEngine::audioSampleTimer;
	e.nameHash = h;
	e.noteCode = (int16_t)noteCode;
	e.velocity = (uint8_t)velocity;
	e.kind = kind;
	dzNoteLog.index++;
}
} // namespace dz
