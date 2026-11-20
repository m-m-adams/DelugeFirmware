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

/*
 * ======================= DEFERRED ALLOCATION ========================
 *
 * The audio routine (AudioEngine::routine(): the song tick plus the render) must not wait on the
 * GeneralMemoryAllocator - an allocation can search the empty-space records, steal Stealables and move lots of memory,
 * and the time it takes counts against the audio deadline. So code that runs in the audio routine asks for memory
 * with a DeferredAlloc instead: request() never allocates there. It queues the request and returns nullptr, and a
 * low-priority task (GeneralMemoryAllocator::serviceDeferred(), registered in registerTasks()) does the real
 * allocation later and puts the pointer in the DeferredAlloc. Until then the effect that wanted the memory just
 * doesn't run (mod FX, delay and grain pass the dry signal through).
 *
 * Outside the audio routine (UI, file loading, MIDI tasks...), request() allocates straight away, exactly like the
 * synchronous API did, so behaviour there is unchanged. Frees work the same way: deallocDeferred() frees at once
 * outside the audio routine, and inside it queues the pointer for the same task.
 *
 * Threading model. The Deluge runs one core with a cooperative scheduler; no interrupt handler touches the GMA or
 * these queues. So a request, a cancellation and the service never run "at the same time" - they only interleave at
 * function-call boundaries:
 *   - The audio routine runs from its scheduler task, and nested from AudioEngine::routineWithClusterLoading() /
 *     routineForSD() inside other code. It never yields, so the service can't run in the middle of a render.
 *   - The service runs from its own task, which the scheduler may also run nested inside another task's yield()
 *     (e.g. an SD-card wait during song loading, while instruments are half built). That's fine because the service
 *     touches nothing but the GMA, its own queues and the DeferredAlloc objects that are registered with it - never
 *     the objects that own them, except through the construct/destroy callbacks, which must only touch the new
 *     memory. It does nothing if the GMA's lock is held or the audio routine is running (audioRoutineLocked), so it
 *     can't re-enter the GMA or interleave with a render that the GMA might have called into.
 *   - Rendezvous is a slot in a fixed table owned by the GMA, not the DeferredAlloc itself: the DeferredAlloc's
 *     destructor (or reset()) cancels its slot. If the allocation is in progress at that moment (only possible if
 *     the GMA called back into code that destroyed the owner), the slot is marked cancelled and the service frees
 *     the memory when the allocation returns instead of writing into the dead owner.
 *   - A DeferredAlloc is registered by address while it has a request queued, so it must not be copied or moved
 *     (it's neither copyable nor movable) and its owner mustn't memmove it.
 *
 * Failure handling: if the request table is full, request() returns nullptr and queues nothing - the next request()
 * call (normally the next render) tries again. If the allocation fails, the request stays queued and is retried
 * with an exponential backoff (about 2.9 ms doubling up to about 1.5 s of audio time).
 */

enum class AllocRegion : uint8_t {
	MAX_SPEED, ///< Internal RAM if possible, then external, then the stealable region (= allocMaxSpeed())
	LOW_SPEED, ///< External, then the stealable region (= allocLowSpeed())
	STEALABLE, ///< The stealable region, marked stealable (= allocStealable()). A construct callback is required: it
	           ///< must construct the Stealable in the memory, since the GMA may call its virtuals from then on
};

class DeferredAlloc {
public:
	/// Called on the new memory right after it's allocated (in the allocating context). Must only touch that memory.
	using ConstructFn = void (*)(void* memory, uint32_t size, void* arg);
	/// Called on the memory right before it's freed, e.g. to run a destructor.
	using DestroyFn = void (*)(void* memory);

	enum class State : uint8_t {
		EMPTY,  ///< nothing allocated or queued
		QUEUED, ///< a request is waiting for the service (or in progress)
		READY,  ///< the memory is here: get() returns it
	};

	DeferredAlloc() = default;
	~DeferredAlloc() { reset(); }
	DeferredAlloc(const DeferredAlloc&) = delete;
	DeferredAlloc& operator=(const DeferredAlloc&) = delete;
	DeferredAlloc(DeferredAlloc&&) = delete;
	DeferredAlloc& operator=(DeferredAlloc&&) = delete;

	/// Get memory of exactly `size` bytes (or more). Returns it if it's already here. Otherwise allocates it now if
	/// that's allowed (not in the audio routine, and `mayAllocateNow`), or else queues a request (if none is queued
	/// yet) and returns nullptr: call again later, typically on the next render. A queued request for another size
	/// is updated to this size; memory that's ready but of another size is freed (deferred) and a new request made.
	/// construct / destroy / arg are remembered with the request.
	void* request(uint32_t size, AllocRegion region = AllocRegion::MAX_SPEED, ConstructFn construct = nullptr,
	              DestroyFn destroy = nullptr, void* arg = nullptr, bool mayAllocateNow = true);

	/// The memory if it's ready, else nullptr. Never allocates.
	[[nodiscard]] void* get() const { return (state_ == State::READY) ? ptr_ : nullptr; }
	[[nodiscard]] State state() const { return state_; }
	[[nodiscard]] bool isPending() const { return state_ == State::QUEUED; }
	/// The size the ready (or queued) memory was requested with.
	[[nodiscard]] uint32_t size() const { return size_; }

	/// Hand the ready memory over to the caller, who then owns it (free it with GMA dealloc / deallocDeferred). The
	/// handle goes back to EMPTY. nullptr if not ready.
	void* take();

	/// Cancel a queued request, or free the ready memory (calling the destroy callback first; the free is deferred
	/// when called in the audio routine). The handle goes back to EMPTY.
	void reset();

	/// Forget the ready memory without freeing it - for a Stealable whose memory the GMA has just stolen.
	void forget() {
		ptr_ = nullptr;
		state_ = State::EMPTY;
	}

private:
	friend class GeneralMemoryAllocator;
	void* volatile ptr_ = nullptr;
	ConstructFn construct_ = nullptr;
	DestroyFn destroy_ = nullptr;
	void* arg_ = nullptr;
	uint32_t size_ = 0;
	volatile State state_ = State::EMPTY;
	AllocRegion region_ = AllocRegion::MAX_SPEED;
	uint8_t slot_ = 0xFF;
};

/// Counters for the deferred allocator and the "no GMA calls in the audio routine" check. Lives in a global with C
/// linkage (deferredAllocStats) so a debugger / the emulator can read it.
struct DeferredAllocStats {
	uint32_t magic;               ///< 0xDEFE4A11
	uint32_t requestsQueued;      ///< requests put in the table
	uint32_t requestsServed;      ///< allocations done by the service and published
	uint32_t requestsFailed;      ///< allocation attempts that failed (will retry with backoff)
	uint32_t requestsCancelled;   ///< requests cancelled before completion
	uint32_t requestsTableFull;   ///< request() calls that found the table full
	uint32_t requestsResized;     ///< queued requests whose size was changed
	uint32_t immediateAllocs;     ///< request() calls that allocated straight away (outside the audio routine)
	uint32_t freesQueued;         ///< deallocDeferred() calls queued from the audio routine
	uint32_t freesServed;         ///< queued frees done by the service
	uint32_t freesQueueFull;      ///< deallocDeferred() calls in the audio routine that had to free synchronously
	uint32_t serviceRuns;         ///< service calls that did some work
	uint32_t maxQueued;           ///< high-water mark of the request table
	uint32_t syncAllocsInAudio;   ///< synchronous GMA allocations (alloc/allocExternal/allocInternal/extend) made
	                              ///< inside the audio routine: should stay 0
	uint32_t syncDeallocsInAudio; ///< synchronous GMA deallocations inside the audio routine
	uint32_t lastSyncAllocCaller; ///< return address of the last synchronous allocation in the audio routine
	uint32_t lastSyncDeallocCaller;
	uint32_t maxLatencySamples; ///< longest time from queueing a request to publishing its memory (audio samples)
	uint32_t sumLatencySamples; ///< sum of that over requestsServed
};
extern "C" DeferredAllocStats deferredAllocStats;
