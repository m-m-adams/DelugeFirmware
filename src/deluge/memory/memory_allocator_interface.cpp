#include "general_memory_allocator.h"

void* allocMaxSpeed(uint32_t requiredSize, void* thingNotToStealFrom = nullptr) {
	return GeneralMemoryAllocator::get().alloc(requiredSize, true, false, thingNotToStealFrom);
}

void* allocLowSpeed(uint32_t requiredSize, void* thingNotToStealFrom = nullptr) {
	return GeneralMemoryAllocator::get().alloc(requiredSize, false, false, thingNotToStealFrom);
}

void* allocStealable(uint32_t requiredSize, void* thingNotToStealFrom = nullptr) {
	void* addr = GeneralMemoryAllocator::get().alloc(requiredSize, false, true, thingNotToStealFrom);
	return reinterpret_cast<void*>(reinterpret_cast<uintptr_t>(addr) | UNCACHED_MIRROR_OFFSET);
}
