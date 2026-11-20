#include "io/debug/alloc_trace.h"
#include "definitions.h"

#if DZ_ALLOC_TRACE

namespace AudioEngine {
extern uint32_t audioSampleTimer;
}

extern uint32_t program_code_start;
extern uint32_t program_code_end;
extern uint32_t __sdram_text_start;
extern uint32_t __sdram_text_end;

extern "C" {
volatile uint8_t dzInAudioRoutine = 0;
volatile uint8_t dzInTick = 0;

struct DzAllocEntry {
	uint32_t site;   // return address of the outermost GMA entry point
	uint32_t size;   // requested size (0 for deallocs)
	uint32_t result; // returned pointer (alloc) / freed pointer (dealloc)
	uint8_t kind;    // dz_alloc::Kind
	uint8_t flags;   // 1 = in song tick
	uint16_t pad;
	uint32_t cycles; // PMU cycles inside the outermost GMA call
	uint32_t timer;  // audioSampleTimer
	uint32_t stack[2];
};
static_assert(sizeof(DzAllocEntry) == 32);

constexpr uint32_t kDzAllocEntries = 16384;

struct DzAllocTrace {
	uint32_t magic;      // 0xD2D2A110
	uint32_t index;      // entries recorded so far (ring position = index % numEntries)
	uint32_t entrySize;  // 32
	uint32_t numEntries; // kDzAllocEntries
	uint32_t countByKind[8];
	uint32_t cyclesByKind[8];
	uint32_t countInTick;
	uint32_t reserved[3];
	DzAllocEntry entries[kDzAllocEntries];
};

PLACE_SDRAM_BSS DzAllocTrace dzAllocTrace;
}

namespace dz_alloc {

static uint8_t depth = 0;

[[gnu::always_inline]] static inline uint32_t cycles() {
	uint32_t c;
	asm volatile("MRC p15, 0, %0, c9, c13, 0" : "=r"(c));
	return c;
}

static inline bool isCode(uint32_t a) {
	return (a >= (uint32_t)&program_code_start && a < (uint32_t)&program_code_end)
	       || (a >= (uint32_t)&__sdram_text_start && a < (uint32_t)&__sdram_text_end);
}

void Scope::begin(Kind kind, uint32_t size, void* returnAddress) {
	active = true;
	if (depth++ == 0) {
		outer = true;
		kind_ = kind;
		size_ = size;
		site_ = (uint32_t)returnAddress;
		c0_ = cycles();
	}
}

[[gnu::noinline]] void Scope::end() {
	depth--;
	if (!outer) {
		return;
	}
	uint32_t c = cycles() - c0_;
	DzAllocTrace& t = dzAllocTrace;
	if (t.magic != 0xD2D2A110) {
		t.magic = 0xD2D2A110;
		t.index = 0;
		t.entrySize = sizeof(DzAllocEntry);
		t.numEntries = kDzAllocEntries;
	}
	DzAllocEntry& e = t.entries[t.index % kDzAllocEntries];
	e.site = site_;
	e.size = size_;
	e.result = result;
	e.kind = kind_;
	e.flags = dzInTick ? 1 : 0;
	e.cycles = c;
	e.timer = AudioEngine::audioSampleTimer;
	// Heuristic backtrace: the first code addresses on the stack above this frame that differ from the site
	uint32_t* sp = (uint32_t*)__builtin_frame_address(0);
	int found = 0;
	e.stack[0] = e.stack[1] = 0;
	for (int i = 0; i < 96 && found < 2; i++) {
		uint32_t v = sp[i];
		if (isCode(v) && v != site_) {
			e.stack[found++] = v;
		}
	}
	t.countByKind[kind_ & 7]++;
	t.cyclesByKind[kind_ & 7] += c;
	if (dzInTick) {
		t.countInTick++;
	}
	t.index++;
}

} // namespace dz_alloc

#endif
