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

// Deferred allocation - see memory/deferred_alloc.h for the design and threading model.

#include "memory/deferred_alloc.h"
#include "memory/general_memory_allocator.h"
#include "processing/engines/audio_engine.h"
#include <algorithm>

extern "C" {
DeferredAllocStats deferredAllocStats = {.magic = 0xDEFE4A11};
}

namespace {

constexpr int32_t kMaxDeferredRequests = 64;
constexpr int32_t kMaxDeferredFrees = 64; // must be a power of 2
constexpr int32_t kMaxAllocsPerService = 8;
constexpr uint32_t kFirstBackoffSamples = 128;    // ~2.9 ms
constexpr uint32_t kMaxBackoffSamples = 1u << 16; // ~1.5 s

enum class SlotState : uint8_t { FREE, QUEUED, IN_PROGRESS, CANCELLED };

struct Slot {
	DeferredAlloc* owner;
	uint32_t size;
	uint32_t seq;       // FIFO order
	uint32_t notBefore; // audioSampleTimer: don't retry a failed allocation before this
	uint32_t backoff;   // samples
	AllocRegion region;
	volatile SlotState state;
};

struct PendingFree {
	void* address;
	DeferredAlloc::DestroyFn destroy;
};

Slot slots[kMaxDeferredRequests];
int32_t numSlotsUsed = 0;
uint32_t nextSeq = 0;

PendingFree frees[kMaxDeferredFrees];
volatile uint32_t freeHead = 0; // written by the audio routine
volatile uint32_t freeTail = 0; // written by the service

[[gnu::always_inline]] inline void compilerBarrier() {
	asm volatile("" ::: "memory");
}

void freeSlot(Slot& slot) {
	slot.owner = nullptr;
	slot.state = SlotState::FREE;
	numSlotsUsed--;
}

} // namespace

// ---------------------------------------------------------------------------------------------------------------------
// DeferredAlloc

void* DeferredAlloc::request(uint32_t size, AllocRegion region, ConstructFn construct, DestroyFn destroy, void* arg,
                             bool mayAllocateNow) {
	GeneralMemoryAllocator& gma = GeneralMemoryAllocator::get();

	if (state_ == State::READY) {
		if (size == size_) {
			return ptr_;
		}
		reset(); // Wrong size: free it (deferred if we're in the audio routine) and ask again below
	}

	construct_ = construct;
	destroy_ = destroy;
	arg_ = arg;
	region_ = region;

	if (state_ == State::QUEUED) {
		if (size != size_) {
			gma.resizeDeferred(*this, size);
		}
		return nullptr;
	}

	// EMPTY
	size_ = size;
	if (mayAllocateNow && !gma.inAudioRoutine() && !gma.lock) {
		void* address = gma.allocInRegion(size, region);
		if (address != nullptr) {
			if (construct != nullptr) {
				construct(address, size, arg);
			}
			ptr_ = address;
			state_ = State::READY;
			deferredAllocStats.immediateAllocs++;
			return address;
		}
		// Failed: queue it, and the service will keep retrying with a backoff
	}

	gma.queueDeferred(*this);
	return nullptr;
}

void* DeferredAlloc::take() {
	if (state_ != State::READY) {
		return nullptr;
	}
	void* address = ptr_;
	ptr_ = nullptr;
	state_ = State::EMPTY;
	return address;
}

void DeferredAlloc::reset() {
	if (state_ == State::QUEUED) {
		GeneralMemoryAllocator::get().cancelDeferred(*this);
	}
	else if (state_ == State::READY) {
		void* address = ptr_;
		ptr_ = nullptr;
		state_ = State::EMPTY;
		GeneralMemoryAllocator::get().deallocDeferred(address, destroy_);
	}
	state_ = State::EMPTY;
}

// ---------------------------------------------------------------------------------------------------------------------
// GeneralMemoryAllocator side

void* GeneralMemoryAllocator::allocInRegion(uint32_t requiredSize, AllocRegion region) {
	switch (region) {
	case AllocRegion::MAX_SPEED:
		return allocMaxSpeed(requiredSize);
	case AllocRegion::LOW_SPEED:
		return allocLowSpeed(requiredSize);
	case AllocRegion::STEALABLE:
		return alloc(requiredSize, false, true, nullptr);
	}
	return nullptr;
}

bool GeneralMemoryAllocator::queueDeferred(DeferredAlloc& handle) {
	for (int32_t i = 0; i < kMaxDeferredRequests; i++) {
		Slot& slot = slots[i];
		if (slot.state != SlotState::FREE) {
			continue;
		}
		slot.owner = &handle;
		slot.size = handle.size_;
		slot.region = handle.region_;
		slot.seq = nextSeq++;
		slot.notBefore = AudioEngine::audioSampleTimer;
		slot.backoff = 0;
		slot.state = SlotState::QUEUED;
		handle.slot_ = i;
		handle.state_ = DeferredAlloc::State::QUEUED;
		numSlotsUsed++;
		deferredAllocStats.requestsQueued++;
		if ((uint32_t)numSlotsUsed > deferredAllocStats.maxQueued) {
			deferredAllocStats.maxQueued = numSlotsUsed;
		}
		return true;
	}
	// Table full: stay EMPTY, the caller will ask again
	deferredAllocStats.requestsTableFull++;
	return false;
}

void GeneralMemoryAllocator::resizeDeferred(DeferredAlloc& handle, uint32_t newSize) {
	handle.size_ = newSize;
	if (handle.slot_ < kMaxDeferredRequests && slots[handle.slot_].owner == &handle) {
		// If it's in progress, the service notices the size changed when the allocation returns and asks again
		slots[handle.slot_].size = newSize;
		slots[handle.slot_].region = handle.region_;
		deferredAllocStats.requestsResized++;
	}
}

void GeneralMemoryAllocator::cancelDeferred(DeferredAlloc& handle) {
	if (handle.slot_ < kMaxDeferredRequests) {
		Slot& slot = slots[handle.slot_];
		if (slot.owner == &handle) {
			if (slot.state == SlotState::IN_PROGRESS) {
				// The service is inside the GMA for this one right now (we've been called back from within it). It
				// will see this and free the memory instead of publishing it to us.
				slot.state = SlotState::CANCELLED;
				slot.owner = nullptr;
			}
			else {
				freeSlot(slot);
			}
			deferredAllocStats.requestsCancelled++;
		}
	}
	handle.slot_ = 0xFF;
	handle.state_ = DeferredAlloc::State::EMPTY;
}

void GeneralMemoryAllocator::deallocDeferred(void* address, DeferredAlloc::DestroyFn destroy) {
	if (address == nullptr) {
		return;
	}
	if (!inAudioRoutine()) {
		if (destroy != nullptr) {
			destroy(address);
		}
		dealloc(address);
		return;
	}
	uint32_t head = freeHead;
	if (head - freeTail >= (uint32_t)kMaxDeferredFrees) {
		// Queue full: free it now (counted as a synchronous dealloc in the audio routine)
		deferredAllocStats.freesQueueFull++;
		if (destroy != nullptr) {
			destroy(address);
		}
		dealloc(address);
		return;
	}
	frees[head & (kMaxDeferredFrees - 1)] = PendingFree{address, destroy};
	compilerBarrier();
	freeHead = head + 1;
	deferredAllocStats.freesQueued++;
}

void GeneralMemoryAllocator::serviceDeferred() {
	// Never re-enter the GMA, and never run inside the audio routine (or while something has locked it out because
	// it's rebuilding the objects it renders)
	if (lock || AudioEngine::audioRoutineLocked || inAudioRoutine()) {
		return;
	}

	bool didSomething = false;

	// Frees first: they can only make room for the allocations
	while (freeTail != freeHead) {
		PendingFree f = frees[freeTail & (kMaxDeferredFrees - 1)];
		compilerBarrier();
		freeTail = freeTail + 1;
		if (f.destroy != nullptr) {
			f.destroy(f.address);
		}
		dealloc(f.address);
		deferredAllocStats.freesServed++;
		didSomething = true;
	}

	for (int32_t n = 0; n < kMaxAllocsPerService && numSlotsUsed > 0; n++) {
		// Oldest queued request that isn't backing off
		uint32_t now = AudioEngine::audioSampleTimer;
		int32_t best = -1;
		for (int32_t i = 0; i < kMaxDeferredRequests; i++) {
			Slot& slot = slots[i];
			if (slot.state != SlotState::QUEUED || (int32_t)(now - slot.notBefore) < 0) {
				continue;
			}
			if (best < 0 || (int32_t)(slot.seq - slots[best].seq) < 0) {
				best = i;
			}
		}
		if (best < 0) {
			break;
		}

		didSomething = true;
		Slot& slot = slots[best];
		slot.state = SlotState::IN_PROGRESS;
		uint32_t size = slot.size;
		void* address = allocInRegion(size, slot.region);

		if (slot.state == SlotState::CANCELLED) {
			// The owner went away while we were allocating
			if (address != nullptr) {
				dealloc(address);
			}
			freeSlot(slot);
			continue;
		}

		if (address == nullptr) {
			slot.backoff = (slot.backoff == 0) ? kFirstBackoffSamples : std::min(slot.backoff * 2, kMaxBackoffSamples);
			slot.notBefore = AudioEngine::audioSampleTimer + slot.backoff;
			slot.state = SlotState::QUEUED;
			deferredAllocStats.requestsFailed++;
			continue;
		}

		if (slot.size != size) {
			// Resized while we were allocating: try again with the new size
			dealloc(address);
			slot.state = SlotState::QUEUED;
			continue;
		}

		DeferredAlloc* owner = slot.owner;
		if (owner->construct_ != nullptr) {
			DeferredAlloc::DestroyFn destroy = owner->destroy_;
			owner->construct_(address, size, owner->arg_);
			if (slot.state == SlotState::CANCELLED) {
				if (destroy != nullptr) {
					destroy(address);
				}
				dealloc(address);
				freeSlot(slot);
				continue;
			}
		}

		// Publish
		owner->ptr_ = address;
		compilerBarrier();
		owner->state_ = DeferredAlloc::State::READY;
		owner->slot_ = 0xFF;
		freeSlot(slot);
		deferredAllocStats.requestsServed++;
	}

	if (deferredMaintenance_ != nullptr) {
		deferredMaintenance_();
	}

	if (didSomething) {
		deferredAllocStats.serviceRuns++;
	}
}

void GeneralMemoryAllocator::noteSyncAllocInAudioRoutine(void* caller) {
	deferredAllocStats.syncAllocsInAudio++;
	deferredAllocStats.lastSyncAllocCaller = (uint32_t)caller;
}

void GeneralMemoryAllocator::noteSyncDeallocInAudioRoutine(void* caller) {
	deferredAllocStats.syncDeallocsInAudio++;
	deferredAllocStats.lastSyncDeallocCaller = (uint32_t)caller;
}
