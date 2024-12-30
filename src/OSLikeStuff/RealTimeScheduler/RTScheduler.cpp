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

TCB* CurrentTCB = nullptr;
void RTScheduler::scheduleSwitch(Time time) {
	//	DISABLE_ALL_INTERRUPTS();
	//
	//	wakeTime = time;
	//	int64_t ticks = time - getSecondsFromStart();
	//	if (ticks < 0) {
	//		ticks = 0.0005 * DELUGE_CLOCKS_PERf; // if we don't have a time yet make sure we're back in .5ms
	//	}
	//	disableTimer(1);
	//	// this breaks if we want to wait more than 2 minutes but we probably don't?
	//	setTimerValue(1, ticks);
	//	// just let it count - a full loop is 2 minutes or so and we'll handle that case manually
	//	setOperatingMode(1, TIMER, true);
	//	enableTimer(1);
	//	ENABLE_INTERRUPTS();
}
void RTScheduler::switchContext() {
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
	TCB newTCB = TCB(stackTop, stackSize, priority);
	auto newStackTop = initializeStack(stackTop, function);
	newTCB.topOfStack = newStackTop;
	rtTaskArray.push_back(newTCB);
	std::sort(rtTaskArray.begin(), rtTaskArray.end());
}
extern uint32_t program_stack_start;
extern uint32_t program_stack_end;
extern "C" {
extern void ContextSwitch(void);
}
void RTScheduler::startWithCurrentThread() {

	TCB newTCB = TCB(&program_stack_end, &program_stack_end - &program_stack_start, 100);
	rtTaskArray.push_back(newTCB);
	CurrentTCB = &rtTaskArray.back();
	// setupAndEnableInterrupt(reinterpret_cast<void (*)(uint32_t)>(ContextSwitch), INTC_ID_OSTM1TINT, 0);
	yieldCPU();
}
void RTScheduler::delayUntil(Time time) {
	CurrentTCB->nextWakeTime = time;
	CurrentTCB->state = ThreadState::waiting;
	yieldCPU();
}

RTScheduler rtScheduler;

extern "C" {
void vTaskSwitchContext(void) {
	rtScheduler.switchContext();
}

void yieldCPU(void) {
	if (CurrentTCB) {
		// we'll just go ahead and call the scheduler now, it'll choose something to run instead of us
		__asm volatile("SWI 0" ::: "memory");
	}
}
}