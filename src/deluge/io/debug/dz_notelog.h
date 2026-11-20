#pragma once
#include <cstdint>
namespace dz {
// kind: 1 voice start (noteOnPostArpeggiator), 2 note-off (noteOffPostArpeggiator)
void noteLog(char const* name, int32_t noteCode, int32_t velocity, uint8_t kind);
} // namespace dz
