/*
 * FreeRTOS Kernel <DEVELOPMENT BRANCH>
 * Copyright (C) 2021 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 *
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
 *
 */
    .eabi_attribute Tag_ABI_align_preserved, 1
    .text
    .arm

    .set SYS_MODE,  0x1f
    .set SVC_MODE,  0x13
    .set IRQ_MODE,  0x12

    /* Hardware registers. */
    .extern ulICCIAR
    .extern ulICCEOIR
    .extern ulICCPMR

    /* Variables and functions. */
    .extern ulMaxAPIPriorityMask
    .extern _freertos_vector_table
    .extern pxCurrentTCB
    .extern vTaskSwitchContext
    .extern vApplicationIRQHandler
    .extern ulPortInterruptNesting
    .extern ulPortTaskHasFPUContext

    .global SWI_Handler
    .global vPortRestoreTaskContext

.macro portSAVE_CONTEXT

    /* Save the LR and SPSR onto the system mode stack before switching to
    system mode to save the remaining system mode registers. */
    SRSDB   sp!, #SYS_MODE
    CPS     #SYS_MODE
    PUSH    {R0-R12, R14}

    /* Save the floating point context */
    FMRX  R1,  FPSCR
    PUSH  {R1}
    VPUSH {D0-D15}
    VPUSH {D16-D31}

    /* Save the stack pointer in the TCB. */
    LDR     R0, pxCurrentTCBConst
    LDR     R1, [R0]
    STR     SP, [R1]

    .endm

; /**********************************************************************/

.macro portRESTORE_CONTEXT

    /* Set the SP to point to the stack of the task being restored. */
    LDR     R0, pxCurrentTCBConst
    LDR     R1, [R0]
    LDR     SP, [R1]

    /* Restore the floating point context*/
    VPOP  {D16-D31}
    VPOP  {D0-D15}
    POP   {R0}
    VMSR  FPSCR, R0

    /* Restore all system mode registers other than the SP (which is already
    being used). */
    POP     {R0-R12, R14}

    /* Return to the task code, loading CPSR on the way. */
    RFEIA   sp!

    .endm

/******************************************************************************
 * SVC handler is used to start the scheduler.
 *****************************************************************************/
.align 4
.type FreeRTOS_SWI_Handler, %function
FreeRTOS_SWI_Handler:
    /* Save the context of the current task and select a new task to run. */
    portSAVE_CONTEXT

    /* Ensure bit 2 of the stack pointer is clear. */
    MOV     r2, sp
    AND     r2, r2, #4
    SUB     sp, sp, r2

    LDR R0, vTaskSwitchContextConst
    BLX R0

    portRESTORE_CONTEXT


/******************************************************************************
 * vPortRestoreTaskContext is used to start the scheduler.
 *****************************************************************************/
.type vPortRestoreTaskContext, %function
vPortRestoreTaskContext:
    /* Switch to system mode. */
    CPS     #SYS_MODE
    portRESTORE_CONTEXT


ulICCIARConst:  .word ulICCIAR
ulICCEOIRConst: .word ulICCEOIR
ulICCPMRConst: .word ulICCPMR
pxCurrentTCBConst: .word pxCurrentTCB
ulCriticalNestingConst: .word ulCriticalNesting
ulPortTaskHasFPUContextConst: .word ulPortTaskHasFPUContext
ulMaxAPIPriorityMaskConst: .word ulMaxAPIPriorityMask
vTaskSwitchContextConst: .word vTaskSwitchContext
vApplicationIRQHandlerConst: .word vApplicationIRQHandler
ulPortInterruptNestingConst: .word ulPortInterruptNesting
vApplicationFPUSafeIRQHandlerConst: .word vApplicationFPUSafeIRQHandler

.end
