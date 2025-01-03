/*
 * SPDX-License-Identifier: MIT
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy of
 * this software and associated documentation files (the "Software"), to deal in
 * the Software without restriction, including without limitation the rights to
 * use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
 * the Software, and to permit persons to whom the Software is furnished to do so,
 * subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
 * FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
 * COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
 * IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
 * CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 *
 * https://www.FreeRTOS.org
 * https://github.com/FreeRTOS
 * Based on the cortex A9 free rtos port assembly
 */
    .eabi_attribute Tag_ABI_align_preserved, 1
    .text
    .arm

/* Standard definitions of mode bits and interrupt (I & F) flags in PSRs */
    .equ    SYS_MODE          , 0x1F
    .equ    SVC_MODE          , 0x13
    .equ    IRQ_MODE          , 0x12
    .equ    INTC_ICCIAR_MASK  , 0x3FF
    .equ    INTC_ICCIAR_ADDR  , 0xE820200C
    .equ    INTC_ICCEOIR_ADDR , 0xE8202010
    .equ    INTC_ICCHPIR_ADDR , 0xE8202018
    .equ    INTC_ICDIPR0_ADDR , 0xE8201400

    /* Variables and functions. */
    .extern CurrentTCB
    .extern vTaskSwitchContext

    .global ContextSwitch
    .global RestoreTaskContext

.macro SAVE_CONTEXT

    /* Save the LR and SPSR onto the system mode stack before switching to
    system mode to save the remaining system mode registers. */
    SRSDB   sp!, #SYS_MODE
    CPS     #SYS_MODE
    PUSH    {R0-R12, R14}

    /* Save the floating point context */
    FMRX  R1,  FPSCR
    VPUSH {D0-D15}
    VPUSH {D16-D31}
    PUSH  {R1}

    /* Save the stack pointer in the TCB. */
    LDR     R0, CurrentTCBConst
    LDR     R1, [R0]
    STR     SP, [R1]

    .endm

.macro RESTORE_CONTEXT

    /* Set the SP to point to the stack of the task being restored. */
    LDR     R0, CurrentTCBConst
    LDR     R1, [R0]
    LDR     SP, [R1]

    /* Restore the floating point context*/
    POP   {R0}
    VPOP  {D16-D31}
    VPOP  {D0-D15}
    VMSR  FPSCR, R0

    /* Restore all system mode registers other than the SP (which is already
    being used). */
    POP     {R0-R12, R14}
    /* Return to the task code, loading CPSR on the way. */
    RFEIA   sp!

    .endm


.align 4
.type ContextSwitch, %function
ContextSwitch:
    // we can't have any other interrupts fire while we do this
	CPSID	i
	DSB
	ISB
    /* Save the context of the current task and select a new task to run. */
    SAVE_CONTEXT

    /* Ensure bit 2 of the stack pointer is clear. */
    MOV     r2, sp
    AND     r2, r2, #4
    SUB     sp, sp, r2

    LDR R0, ChooseNextThreadConst
    BLX R0

    RESTORE_CONTEXT

.align 4
read_intc_icciar_addr:
	PUSH	{r1, r12}
	PUSH	{r3-r4, r12}

    /*; ++REE_SS Addressing ARM Errata 801120 */
    /*; Perform a dummy read to - ensure subsequent ICCIAR data */
    /*; will be correct */
    LDR     r2, =INTC_ICCHPIR_ADDR
    LDR     r2, [r2]

    /*; Attempt to activate interrupt and get its ID */
    /*; Load in to R3 - if valid it will be used later as ICCIAR */
    LDR     r2, =INTC_ICCIAR_ADDR
    LDR     r3, [r2]

    /*; Extract the interrupt ID (removing the SGI source CPU ID) */
    LDR     r1, =INTC_ICCIAR_MASK
    AND     r3, r3, r1

    /*; Read data of 0x0 (SGI ID0 from CPU0) is possibly affected by 733075 */
    CMP     r3, #0
    BEQ     errata_733075_workaround

    /*; Interrupt IDs 0x3FE and 0x3FF are also possibly affected */
    LDR     r1, =1022
    CMP     r3, r1
    BGE     errata_733075_workaround

    B       post_733075_workaround
.align 4
errata_733075_workaround:
    /*; Perform a read from ulICDIPR0 and write value back */
    /*; It is sufficient to write the value that is already in the register. */
    /*; You can obtain the value to be written to the ICDIPR0 register by */
    /*; reading from it, or through other means" */
    LDR     r2, =INTC_ICDIPR0_ADDR
    LDR     r0, [r2]
    STR     r0, [r2]
    DSB

    LDR     r2, =INTC_ICCHPIR_ADDR
    LDR     r2, [r2]

    /*; Attempt to activate interrupt and get its ID */
    /*; Load in to R3 - if valid it will be used later as ICCIAR */
    LDR     r2, =INTC_ICCIAR_ADDR
    LDR     r3, [r2]
.align 4
post_733075_workaround:
    MOV     r0, r3
	POP		{r3-r4, r12}
	POP		{r1, r12}
	B       icciar_read_complete

/* ================================================================== */
/* Entry point for the FIQ handler */
/* ================================================================== */
    .global  irq_handler
    .global  fiq_handler
.align 4
fiq_handler:
    SUB        lr, lr, #4
    SRSDB    sp!, #SYS_MODE
    CPS        #SYS_MODE
    PUSH    {r0-r3, r12}
    LDR     r2, =INTC_ICCIAR_ADDR
    LDR        r0, [r2]
    PUSH    {r0}
    MOV        r1, sp
    AND        r1, r1, #4
    SUB        sp, sp, r1
    PUSH    {r1, lr}
    BL    fiq_handler_interrupt
    POP        {r1, lr}
    ADD        sp, sp, r1
    POP        {r0}
    LDR     r2, =INTC_ICCEOIR_ADDR
    STR        r0, [r2]
    POP        {r0-r3, r12}

fiq_handler_end:
    RFEIA    sp!

/* ================================================================== */
/* Entry point for the IRQ handler */
/* ================================================================== */
    .func irq_handler
    .type irq_handler, %function
    .align 4
irq_handler:
    SUB        lr, lr, #4                   // Make sure interrupted instruction gets executed after returining
    //SRSDB    sp!, #SYS_MODE
	/* Push the return address and SPSR onto the irq mode stack*/
	PUSH	{lr}
	MRS		lr, SPSR
	PUSH	{lr}

    CPS        #SVC_MODE

    PUSH    {r0-r4, r12}

    /* Increment nesting count.  r3 holds the address of ulPortInterruptNesting
    for future use.  r1 holds the original ulPortInterruptNesting value for
    future use. */
    LDR		r3, ulPortInterruptNestingConst
    LDR		r1, [r3]
    ADD		r4, r1, #1
    STR		r4, [r3]

    B		read_intc_icciar_addr
icciar_read_complete:

    /* Ensure bit 2 of the stack pointer is clear.  r2 holds the bit 2 value for
	future use.  Guard against the start of the stack not being 8-byte aligned */
    MOV        r1, sp
    AND        r1, r1, #4
    SUB        sp, sp, r1

    PUSH	{r0-r4, lr}  /* Call the interrupt handler.  r4 pushed to maintain alignment. */
    BL        INTC_Handler_Interrupt
    POP		{r0-r4, lr}
    ADD        sp, sp, r1

    // Newer example has barriers here
	CPSID	i
	DSB
	ISB

    LDR     r2, =INTC_ICCEOIR_ADDR
    STR        r0, [r2]
	/* Restore the old nesting count. */
	STR		r1, [r3]

	/* A context switch is never performed if the nesting count is not 0. */
	CMP		r1, #0
	BNE		exit_without_switch

	/* Did the interrupt request a context switch?  r1 holds the address of
	ulPortYieldRequired and r0 the value of ulPortYieldRequired for future
	use. */
	LDR		r1, =YieldRequired
	LDR		r0, [r1]
	CMP		r0, #0
	BNE		switch_before_exit

    //RFEIA    sp!
exit_without_switch:
    /* Restore used registers, LR_irq and SPSR before returning. */
    POP        {r0-r4, r12}
    CPS		#IRQ_MODE
	POP		{LR}
	MSR		SPSR_cxsf, LR
	POP		{LR}
	MOVS	PC, LR //return from exception without popping anything from stack

switch_before_exit:
	/* A context swtich is to be performed.  Clear the context switch pending
	flag. */
	MOV		r0, #0
	STR		r0, [r1]

	/* Restore used registers, LR-irq and SPSR before saving the context
	to the task stack. */
	POP		{r0-r4, r12}
	CPS		#IRQ_MODE
	POP		{LR}
	MSR		SPSR_cxsf, LR
	POP		{LR}
	SAVE_CONTEXT

	/* Call the function that selects the new task to execute.
	vTaskSwitchContext() if vTaskSwitchContext() uses LDRD or STRD
	instructions, or 8 byte aligned stack allocated data.  LR does not need
	saving as a new LR will be loaded by portRESTORE_CONTEXT anyway. */
	LDR		R0, ChooseNextThreadConst
	BLX		R0
	/* Restore the context of, and branch to, the task selected to execute
	next. */

	RESTORE_CONTEXT



Literals3:
ulPortInterruptNestingConst: .word ulPortInterruptNesting
ChooseNextThreadConst: .word ChooseNextThread
CurrentTCBConst: .word CurrentTCB
    .endfunc
    .end
