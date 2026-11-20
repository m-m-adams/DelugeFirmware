#include "general_memory_allocator.h"
#include "io/debug/alloc_trace.h"

void* allocMaxSpeed(uint32_t requiredSize, void* thingNotToStealFrom = nullptr) {
	DZ_ALLOC_SCOPE(dz_alloc::ALLOC, requiredSize);
	return GeneralMemoryAllocator::get().alloc(requiredSize, true, false, thingNotToStealFrom);
}

void* allocLowSpeed(uint32_t requiredSize, void* thingNotToStealFrom = nullptr) {
	DZ_ALLOC_SCOPE(dz_alloc::ALLOC, requiredSize);
	return GeneralMemoryAllocator::get().alloc(requiredSize, false, false, thingNotToStealFrom);
}
