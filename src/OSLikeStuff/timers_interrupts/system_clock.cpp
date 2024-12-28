//
// Created by Mark Adams on 2024-12-28.
//
extern "C" {
#include "RZA1/ostm/ostm.h"
#include "clock_type.h"

bool running = false;
Time lastTime = 0;
Time runningTime = 0;

Time getTimerValueSeconds(int timerNo) {
	Time seconds = getTimerValue(timerNo) * ONE_OVER_CLOCK;
	return seconds;
}
/// return a monotonic timer value in seconds from when the task manager started
Time getSecondsFromStart() {
	auto timeNow = getTimerValueSeconds(0);
	if (timeNow < lastTime) {
		runningTime += rollTime;
	}
	runningTime += timeNow - lastTime;
	lastTime = timeNow;
	return runningTime;
}
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
}
