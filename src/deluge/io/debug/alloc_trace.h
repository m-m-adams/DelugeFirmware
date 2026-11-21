// Allocation trace for perf experiment E4 (not for merging): records every GeneralMemoryAllocator call made while
// AudioEngine::routine() is running (tick or render), with the outermost caller's return address, a few code
// addresses found on the stack (heuristic backtrace), size, result and PMU cycles. Read with
// /home/user/perfwork/tools/alloc_trace.py after a QMP memsave of dzAllocTrace.
#pragma once

#include <cstdint>

#define DZ_ALLOC_TRACE 1

namespace dz_alloc {

enum Kind : uint8_t {
	ALLOC = 1,        // GeneralMemoryAllocator::alloc (allocMaxSpeed / allocLowSpeed / allocStealable / delugeAlloc)
	ALLOC_EXTERNAL,   // allocExternal (operator new, external_allocator)
	ALLOC_INTERNAL,   // allocInternal
	DEALLOC,          // dealloc / delugeDealloc / operator delete
	DEALLOC_EXTERNAL, // deallocExternal
	EXTEND,
	SHORTEN,
};

#if DZ_ALLOC_TRACE
extern "C" volatile uint8_t dzInAudioRoutine; // set by AudioEngine::routine()
extern "C" volatile uint8_t dzInTick;         // set around tickSongFinalizeWindows()

struct Scope {
	[[gnu::always_inline]] Scope(Kind kind, uint32_t size, void* returnAddress) {
		if (dzInAudioRoutine) [[unlikely]] {
			begin(kind, size, returnAddress);
		}
	}
	[[gnu::always_inline]] ~Scope() {
		if (active) [[unlikely]] {
			end();
		}
	}
	void setResult(void* p) { result = (uint32_t)p; }
	void begin(Kind kind, uint32_t size, void* returnAddress);
	void end();
	bool active = false;
	bool outer = false;
	Kind kind_ = ALLOC;
	uint32_t size_ = 0;
	uint32_t site_ = 0;
	uint32_t c0_ = 0;
	uint32_t result = 0;
};

/// Memory/cache pressure counters (dzMemStats): stealable/external free bytes, cache reclaims (steals), late clusters
void reclaimDone(uint32_t cycles, uint32_t sizeNeeded, bool found);
void lateCluster();
void snapshotMemory(); ///< call now and then from the audio routine: sums the regions' empty spaces
[[gnu::always_inline]] inline uint32_t pmuCycles() {
	uint32_t c;
	asm volatile("MRC p15, 0, %0, c9, c13, 0" : "=r"(c));
	return c;
}

#define DZ_ALLOC_SCOPE(kind, size) dz_alloc::Scope dzAllocScope_(kind, size, __builtin_return_address(0))
#define DZ_ALLOC_RESULT(p) dzAllocScope_.setResult(p)
#else
#define DZ_ALLOC_SCOPE(kind, size)
#define DZ_ALLOC_RESULT(p)
#endif

} // namespace dz_alloc
