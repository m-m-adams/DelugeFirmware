//
// Created by Mark Adams on 2024-12-28.
//
#include "io/debug/log.h"
#include "timers_interrupts.h"
extern "C" {
#include "RZA1/ostm/ostm.h"
#include "clock_type.h"

bool running = false;
Time lastTime = 0;
Time runningTime = 0;

void startSystemClock() {
	if (!running) {
		disableTimer(0);
		setTimerValue(0, 0);
		// just let it count - a full loop is 2 minutes or so and we'll handle that case manually
		setOperatingMode(0, FREE_RUNNING, false);
		enableTimer(0);
		running = true;
	}
}
Time getTimerValueSeconds(int timerNo) {
	if (!running) {
		startSystemClock();
	}
	Time seconds = (dTime)getTimerValue(timerNo);
	return seconds;
}

/// return a monotonic timer value in seconds from when the task manager started
Time getSecondsFromStart() {
	DISABLE_ALL_INTERRUPTS();
	auto timeNow = getTimerValueSeconds(0);
	if (timeNow < lastTime) {
		runningTime += rollTime;
		D_PRINTLN("Sys clock rolled");
	}
	runningTime += timeNow - lastTime;
	lastTime = timeNow;
	ENABLE_INTERRUPTS();
	return runningTime;
}
}
