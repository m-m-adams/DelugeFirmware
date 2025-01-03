//
// Created by Mark Adams on 2024-12-24.
//
extern "C" {
#include "RZA1/ostm/ostm.h"
#include "scheduler_api.h"
}
#include "RTScheduler.hpp"

#include "RZA1/intc/devdrv_intc.h"
#include "io/debug/log.h"
#include "timers_interrupts/timers_interrupts.h"
#include <algorithm>

TCB* CurrentTCB = nullptr;
constexpr int delay0_5ms = 0.001 * DELUGE_CLOCKS_PERf;
void RTScheduler::scheduleSwitch(Time time) {

	wakeTime = time;
	int64_t ticks = time - getSecondsFromStart();
	ticks = std::max<int64_t>(ticks, delay0_5ms);
	disableTimer(1);
	setOperatingMode(1, TIMER, false);
	// this breaks if we want to wait more than 2 minutes but we probably don't?
	setTimerValue(1, ticks);
	enableTimer(1);
}
void RTScheduler::switchContext() {
	if (!running) [[unlikely]] {
		return;
	}
	wakeTime = std::numeric_limits<dTime>::max();
	Time currentTime = getSecondsFromStart();
	auto currentPriority = 255;
	for (TCB& tcb : rtTaskArray) {
		// if it's waiting and the current time is passed its wake time
		// (e.g. it was too low priority to wake on time)
		if (tcb.getWakeTime() < currentTime) {
			tcb.endWait();
		}
		// then if it's a higher priority
		if (tcb.priority < currentPriority) {
			// switch to it
			if (tcb.isRunnable()) {
				CurrentTCB = &tcb;
				wakeTime = 0;
				currentPriority = tcb.priority;
			}
			// or schedule a switch to it
			else {
				auto n = tcb.getWakeTime();
				if (n.has_value()) {
					wakeTime = n.value();
				}
			}
		}
	}
	scheduleSwitch(wakeTime);
}
#define THUMB_MODE_ADDRESS (0x01UL)
#define THUMB_MODE_BIT ((uint32_t)0x20)
#define FPU_REGISTER_WORDS ((32 * 2) + 1)

void threadExit(void) {
	freezeWithError("OHNO");
}
uint32_t* RTScheduler::initializeStack(uint32_t* stackTop, TaskHandle function) {
	/* Setup the initial stack of the task.  The stack is set exactly as
	 * expected by the portRESTORE_CONTEXT() macro.
	 *
	 * The fist real value on the stack is the status register, which is set for
	 * system mode, with interrupts enabled.  A few NULLs are added first to ensure
	 * GDB does not try decoding a non-existent return address. */
	*stackTop = (uint32_t)NULL;
	stackTop--;
	*stackTop = (uint32_t)NULL;
	stackTop--;
	*stackTop = (uint32_t)NULL;
	stackTop--;
	*stackTop = (uint32_t)0x1f; /* System mode, ARM mode, IRQ enabled FIQ enabled. */

	if (((uint32_t)function & THUMB_MODE_ADDRESS) != 0x00UL) {
		/* The task will start in THUMB mode. */
		*stackTop |= THUMB_MODE_BIT;
	}

	stackTop--;

	/* Next the return address, which in this case is the start of the task. */
	*stackTop = (uint32_t)function;
	stackTop--;

	/* Next all the registers other than the stack pointer. */
	*stackTop = (uint32_t)threadExit; /* R14 */
	stackTop--;
	*stackTop = (uint32_t)0x12121212; /* R12 */
	stackTop--;
	*stackTop = (uint32_t)0x11111111; /* R11 */
	stackTop--;
	*stackTop = (uint32_t)0x10101010; /* R10 */
	stackTop--;
	*stackTop = (uint32_t)0x09090909; /* R9 */
	stackTop--;
	*stackTop = (uint32_t)0x08080808; /* R8 */
	stackTop--;
	*stackTop = (uint32_t)0x07070707; /* R7 */
	stackTop--;
	*stackTop = (uint32_t)0x06060606; /* R6 */
	stackTop--;
	*stackTop = (uint32_t)0x05050505; /* R5 */
	stackTop--;
	*stackTop = (uint32_t)0x04040404; /* R4 */
	stackTop--;
	*stackTop = (uint32_t)0x03030303; /* R3 */
	stackTop--;
	*stackTop = (uint32_t)0x02020202; /* R2 */
	stackTop--;
	*stackTop = (uint32_t)0x01010101; /* R1 */
	stackTop--;

	/* zero all the fpu registers*/
	stackTop -= FPU_REGISTER_WORDS;
	memset(stackTop, 0x00, FPU_REGISTER_WORDS * sizeof(uint32_t));

	return stackTop;
}
void RTScheduler::addThread(uint32_t* stackTop, size_t stackSize, TaskHandle function, uint8_t priority) {
	TCB newTCB = TCB(stackTop, stackSize, priority, "audio thread");
	auto newStackTop = initializeStack(stackTop, function);
	newTCB.stackPointer = newStackTop;
	rtTaskArray.push_back(newTCB);
	std::sort(rtTaskArray.begin(), rtTaskArray.end());
}
extern uint32_t program_stack_start;
extern uint32_t program_stack_end;

void RTScheduler::startWithCurrentThread() {
	running = true;
	TCB newTCB = TCB(&program_stack_end, &program_stack_end - &program_stack_start, 100, "main thread");
	rtTaskArray.push_back(newTCB);
	CurrentTCB = &rtTaskArray.back();

	D_PRINTLN("clock tick started");
	// don't need to set the SP yet - the context will be stashed there when we yield
	yieldCPU();
}
void RTScheduler::delayUntil(Time time) {
	CurrentTCB->nextWakeTime = time + getSecondsFromStart();
	CurrentTCB->state = ThreadState::waiting;
	yieldCPU();
}

RTScheduler rtScheduler;

extern "C" {
#pragma GCC push_options
#pragma GCC optimize("align-functions=16")
volatile uint32_t ulPortInterruptNesting = 0;
volatile uint32_t YieldRequired = 0;
void ContextSwitchInterrupt(uint32_t intsense) {
	if (CurrentTCB) {
		YieldRequired = 1;
	}
	disableTimer(1);
}
void ChooseNextThread(void) {
	rtScheduler.switchContext();
}

void yieldCPU(void) {
	YieldRequired = 0;
	disableTimer(1);
	if (CurrentTCB) {
		// we'll just go ahead and call the scheduler now, it'll choose something to run instead of us
		__asm volatile("SWI 0" ::: "memory");
	}
}
#pragma GCC pop_options
}