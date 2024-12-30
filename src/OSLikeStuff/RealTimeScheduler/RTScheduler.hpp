//
// Created by Mark Adams on 2024-12-24.
//

#ifndef DELUGE_RTSCHEDULER_HPP
#define DELUGE_RTSCHEDULER_HPP

#include "scheduler_api.h"
#include "timers_interrupts/system_clock.h"
#include "util/container/static_vector.hpp"
#include <array>
#include <optional>

enum class ThreadState {
	ready,
	waiting,
};

struct TCB {
	volatile uint32_t* topOfStack{0};
	uint32_t* bottomOfStack{0};
	Time nextWakeTime{0};
	uint8_t priority;
	ThreadState state{ThreadState::ready};

	TCB(uint32_t* topOfStack, size_t stackSize, uint8_t priority)
	    : topOfStack(topOfStack), bottomOfStack(topOfStack - stackSize), priority(priority) {}
	TCB() = default;
	// priorities are descending (0 is max)
	bool operator<(const TCB& another) const { return priority > another.priority; }
	bool isRunnable() { return state == ThreadState::ready; }
	void endWait() {
		state = ThreadState::ready;
		nextWakeTime = Time(0);
	}
	std::optional<Time> getWakeTime() {
		if (state == ThreadState::waiting) {
			return nextWakeTime;
		}
		return {};
	};
};

extern TCB* CurrentTCB;

class RTScheduler {

	deluge::static_vector<TCB, 8> rtTaskArray;
	Time wakeTime{std::numeric_limits<dTime>::max()};
	void scheduleSwitch(Time time);
	uint32_t* initializeStack(uint32_t* stackTop, TaskHandle function);

public:
	RTScheduler() = default;
	void switchContext();

	void addThread(uint32_t* stackTop, size_t stackSize, TaskHandle function, uint8_t priority);
	void startWithCurrentThread();
	void yield();
	void delayUntil(Time time);
};

extern RTScheduler rtScheduler;

extern "C" {
extern void vTaskSwitchContext(void);
}

#endif // DELUGE_RTSCHEDULER_HPP