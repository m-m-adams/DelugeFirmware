# GDB script to catch ARM "bkpt 1" and advance the PC after hitting it (makes it work like a gdb breakpoint)
catch signal SIGTRAP
commands
  # check if it is a thumb BKPT (0xbe) instruction:
  if (*(unsigned char*)($pc+1)) == 0xbe
    # check if it is a BKPT #1 instruction:
    if (*(unsigned char*)($pc)) == 0x01
      # yes: move PC after bkpt instruction
      set $pc=$pc+2
    end
  end
    # check if it is an ARM BKPT (0xe120) instruction:
    if (*(uint16_t*)($pc+2)) == 0xe120
      # check if it is a BKPT #1 instruction. The 7 should be ignored, the 16 meaningful bits are the first :
      if (*(uint16_t*)($pc)) == 0x0071
        # yes: move PC after bkpt instruction
        set $pc=$pc+4
      end
    end
end