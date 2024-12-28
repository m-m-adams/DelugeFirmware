//
// Created by Mark Adams on 2024-12-24.
//

#ifndef DELUGE_RTSCHEDULER_HPP
#define DELUGE_RTSCHEDULER_HPP
#include "RZA1/ostm/ostm.h"
#include "scheduler_api.h"
#include "timers_interrupts/system_clock.h"
#include <array>
#include <optional>

enum class State {
	ready,
	waiting,
};

class TCB {
	volatile uint32_t* topOfStack{nullptr};
	uint32_t* bottomOfStack{nullptr};
	size_t stackSize{0};
	Time nextWakeTime{0};
	uint8_t priority{255};
	State state{State::waiting};

public:
	// priorities are descending (0 is max)
	bool operator<(const TCB& another) const { return priority > another.priority; }
	bool isRunnable() { return state == State::ready; }
	void endWait() { state = State::ready; }
	std::optional<Time> getWakeTime() {
		if (state == State::waiting) {
			return nextWakeTime;
		}
		return {};
	};
	void setWakeTime(Time time) { nextWakeTime = time; }
};

extern TCB* CurrentTCB;

class RTScheduler {
	std::array<TCB, 4> rtTaskArray{};
	Time wakeTime;
	void scheduleSwitch(Time time) {
		if (time > wakeTime) {
			return; // we'll wake up before then anyway
		}
		wakeTime = time;
		int64_t ticks = time - getSecondsFromStart();
		disableTimer(1);
		// this breaks if we want to wait more than 2 minutes but we probably don't?
		setTimerValue(1, ticks);
		// just let it count - a full loop is 2 minutes or so and we'll handle that case manually
		setOperatingMode(1, TIMER, true);
		enableTimer(1);
	}
	void switchContext() {
		Time nextSwitch = 0;
		Time currentTime = getSecondsFromStart();
		CurrentTCB = rtTaskArray.data();
		for (TCB& tcb : rtTaskArray) {
			// if it's waiting and the current time is passed its wake time
			// (e.g. it was too low priority to wake on time)
			if (tcb.getWakeTime() < currentTime) {
				tcb.endWait();
			}
			// then if it's a higher priority
			if (*CurrentTCB < tcb) {
				// switch to it
				if (tcb.isRunnable()) {
					CurrentTCB = &tcb;
					nextSwitch = 0;
				}
				// or schedule a switch to it
				else {
					auto n = tcb.getWakeTime();
					if (tcb.getWakeTime() < wakeTime) {
						nextSwitch = n.value();
					}
				}
			}
		}
		if (nextSwitch > Time(0)) {
			scheduleSwitch(nextSwitch);
		}
	}
};

#endif // DELUGE_RTSCHEDULER_HPP