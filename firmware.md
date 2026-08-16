# The MV7 firmware


I examined the file `MonoIn_StereoOut_24b_96KHz_1.cyacd` from `MV7.1.2.19.pack`. That file holds
MCU application version `0.0.52.0`. I also read all 128 KB of the EEPROM from a connected
microphone with package version 1.2.17.

## 1. The container format

A `.cyacd` file is the Cypress PSoC bootloader format. It has a header of 12 characters. Then it
has one line for each flash row.

```
Header: 100F11A0 00 01        siliconID=0x100F11A0  rev=0x00  checksumType=0x01 (CRC-16)
Row:    :AA RRRR LLLL <data...> CC
        arrayID(1) row(2) length(2) data(length) checksum(1)
```

Both images use array 0 and rows of 256 bytes. Each image has 409 data rows and one metadata row.

Each row ends with a checksum byte. It is the two's complement of the sum of the other bytes in
the row. All 410 rows of each image agree with this rule. The `checksumType` field in the header
selects the checksum of the bootloader protocol, not the checksum of the row. A value of `0x01`
selects CRC-16.

| Image | Rows | Flash addresses | Metadata row |
|---|---|---|---|
| `..._1.cyacd` | 43 to 451 | `0x02B00` to `0x1C3FF` | 1023 (`0x3FF00`) |
| `..._2.cyacd` | 533 to 941 | `0x21500` to `0x3ADFF` | 1022 (`0x3FE00`) |

98.6 % of the bytes in the two images are the same. They are one firmware with two link
addresses. This is the usual Cypress **dual-application bootloadable** design. The bootloader
holds rows 0 to 42, which is 11 KB. The package does not contain those rows. The bootloader writes
to the application slot that is not in use. Therefore an update that fails does not stop the
microphone.

The metadata is the last 64 bytes of the metadata row, at row offset `0xC0`:

| Offset | Field | Image 1 | Image 2 |
|---|---|---|---|
| +0 | Application checksum | `0x7C` | `0xA6` |
| +1 | Application entry point (u32) | `0x00002B11` | `0x00021511` |
| +5 | Last bootloader row (u16) | `42` | `532` |
| +9 | Application length in bytes (u32) | `104704` | `104704` |

The two entry points are the same as the reset vectors at the start of each slot. This agreement
shows that the field decode is correct.

The flash map is:

```
0x00000 - 0x02AFF   bootloader, 11 KB, factory, not in the package
0x02B00 - 0x1C3FF   application slot 1, 102 KB
0x1C400 - 0x214FF   not in use
0x21500 - 0x3ADFF   application slot 2, 102 KB
0x3AE00 - 0x3FDFF   not in use
0x3FE00 - 0x3FEFF   metadata for slot 2
0x3FF00 - 0x3FFFF   metadata for slot 1
```

## 2. The silicon

No document in the package gives the part number. I found these properties in the image.

The core is a **Cortex-M0** with the ARMv6-M architecture. GCC used the `__gnu_thumb1_case_uqi`
jump-table function, which is only for Thumb-1 targets. The image has 102 KB of code with no
`MOVW`, no `MOVT`, no `TBB` and no `TBH` instruction. An ARMv7-M build of this size contains many
of them.

The initial `MSP` value is `0x20008000`. Therefore the SRAM is 32 KB at address `0x20000000`. The
bootloader uses 1024 rows of 256 bytes, which is 256 KB of flash.

The peripheral addresses are those of the PSoC 4 family, not the PSoC 5 family:

| Base address | Block |
|---|---|
| `0x40010000` plus `0x100` for each port | HSIOM port select |
| `0x40040000` plus `0x100` for each port | GPIO ports, up to `0x40040D00`, which is 14 ports |
| `0x40100000` and `0x40101800` | CPUSS |
| `0x40250000`, `0x40260000`, `0x40270000` | Three SCB blocks for I²C, SPI or UART |
| `0x402C0000` and `0x402C2000` | USB full-speed device |

The offsets in the SCB blocks agree with the Cypress SCB register map. The offsets `+0x60`,
`+0x64`, `+0x68` and `+0x6C` are `I2C_CTRL`, `I2C_STATUS`, `I2C_M_CMD` and `I2C_S_CMD`. The
offsets `+0x200` and `+0x240` are the transmit registers. The offsets `+0x300` and `+0x340` are
the receive registers. The offset `+0xF00` holds the interrupt registers.

The first instructions after reset load the address `0x400F701C`.

These properties together give a **PSoC 4200L class part**, which is the `CY8C424x` series. I did
not find the part number for the silicon ID `0x100F11A0`.

The exception vector table in flash holds only `MSP`, `Reset`, `NMI` and `HardFault`. The firmware
moves the other vectors to SRAM with the `VTOR` register. Therefore you cannot read the interrupt
vectors from the image.

## 3. The firmware modules

The assertion text `INFO: (%d) %s %s line %d` gives five source file names:

| File | Function |
|---|---|
| `Application.c` | Main loop, state, command dispatch |
| `CodecDspControl.c` | Codec and DSP start, and register access |
| `EEPROM.c` | The external memory driver and the file directory |
| `LedInterface.c` | Three I²C LED driver chips and 23 LEDs |
| `Meter.c` | Audio level meters |

Ghidra found 778 functions in the image. These are the important ones:

| Address | Function |
|---|---|
| `0x0000a4cc` | Console service: read a line, divide it, dispatch it, test the level |
| `0x0000a370` | Tokenizer. It divides at each space and tab. Maximum 10 tokens. |
| `0x0000a05c` | Reply transmitter. Maximum 125 bytes, divided between two reports. |
| `0x00004f18` | Console start. It reads the default privilege level from EEPROM `0x8D`. |
| `0x00004f74` | Asynchronous event transmitter |
| `0x0000a72c` | The `su` command |
| `0x0000a790` | The `help` and `?` commands |
| `0x0000b260` | The upload data handler |
| `0x0000b240` | The update session flag at EEPROM `0xABF` |
| `0x0000ae60` | EEPROM read-back comparison and checksum |
| `0x000198b4` | The command table, 48 entries of 20 bytes |

This is the dispatch code:

```c
for (i = 0; i < cmdCount; i++) {
    e = table + i * 0x14;
    if (strcmp(argv[0], e->name) == 0 && e->level <= state->level) {
        ret = e->handler(argc, argv);
        break;
    }
}
if (!matched) {
    sprintf(buf32, "No such command: %s\n", argv[0]);   /* see security.md */
    send(buf32);
    strcpy(buf32, "Enter 'help' or '?' for help.\n");
    send(buf32);
}
```

## 4. The external EEPROM

A serial memory of 128 KB connects to one of the SCB blocks. The `getEE` command accepts addresses
from `0x00000` to `0x1FFFF`. It rejects the address `0x3FF00`. Therefore the memory is 128 KB.
Only the first 21 KB holds data. All bytes from `0x5500` up are `0xFF`.

This map comes from the memory data and from the `fileLoc` command:

| Addresses | Contents |
|---|---|
| `0x0080` to `0x008F` | Configuration. Byte `0x8D` is the **privilege level after power-on**. |
| `0x0090` to `0x0097` | The package version text, written by `pkgVersion` |
| `0x0098` to `0x009F` | The MCU firmware version text |
| `0x00A0` to `0x00A3` | The DSP version. `00 00 05 16` is version 0.0.5.22. |
| `0x00A4` to `0x00BE` | The board serial number, 27 bytes of ASCII |
| `0x00C0` to `0x00CB` | The Shure SAP number, 12 bytes of ASCII |
| `0x0180` to `0x0197` | Twelve 16-bit values, the LED brightness curve |
| `0x0200` to `0x0293` | DSP block descriptors, a copy of `BLOCKDATA.dat` |
| `0x0ABF` | The update session flag. The value `0x15` means "the file set is complete". |
| `0x0AC0` to `0x0ACA` | The active slot number for each of the 11 files |
| `0x0AE0` to `0x0AEA` | The staged slot number for each of the 11 files |
| `0x0B00` to `0x0B2B` | File directory copy A: 11 end addresses of 4 bytes |
| `0x0B80` to `0x0BAB` | File directory copy B |
| `0x0C00` to `0x2BDE` | `MV7DSP44K.hex` |
| `0x2C37` to `0x4CDE` | `MV7DSP48K.hex` |
| `0x4D37` to `0x54AE` | `MODE1.dat` to `MODE7.dat`, `CAPABILITIES.dat`, `BLOCKDATA.dat` |
| `0x5500` to `0x1FFFF` | Erased |

The `fileLoc` command gave this file table from the device:

| ID | Address | Name | Version |
|---|---|---|---|
| 0 | `0x0C00` | `MV7DSP44K.hex` | 0.0.5.22 |
| 1 | `0x2D00` | `MV7DSP48K.hex` | 0.0.5.22 |
| 2 | `0x4E00` | `MODE1.dat` | 0.0.5.22 |
| 3 | `0x4EC0` | `MODE2.dat` | 0.0.5.22 |
| 4 | `0x4F80` | `MODE3.dat` | 0.0.5.22 |
| 5 | `0x5040` | `MODE4.dat` | 0.0.5.22 |
| 6 | `0x5100` | `MODE5.dat` | 0.0.5.22 |
| 7 | `0x51C0` | `MODE6.dat` | 0.0.5.22 |
| 8 | `0x5280` | `MODE7.dat` | 0.0.5.22 |
| 9 | `0x5340` | `CAPABILITIES.dat` | 0.0.5.22 |
| 10 | `0x53C0` | `BLOCKDATA.dat` | 0.0.5.22 |

The command `fileLoc 11` and higher numbers give `[Failed]`.

All 11 bytes at `0x0AC0` were `01` on this microphone. Therefore all 11 files are in slot 1.

## 5. The DSP

### 5.1 Identification

The DSP is an **Analog Devices ADAU1761**. This is a SigmaDSP audio codec: it contains the ADCs,
the DACs, the headphone amplifier, the microphone bias and a small DSP core. No file in the
package or in MOTIV Mix gives this part number. I found it from the register map.

This evidence identifies the part:

* The MCU uses the I2C address `0x38` with a 16-bit address that is big-endian first. The ADAU1761
  uses the addresses `0x38` to `0x3B`, which the `ADDR0` and `ADDR1` pins select.
* The control registers are from `0x4000` to `0x40FA`. The last ADAU1761 register is `0x40FA`.
* The firmware writes 75 registers. Each address agrees with an ADAU1761 register name.
* The multi-byte writes stop at the correct register group limits. These are the examples:
  * 8 bytes from `0x4009` to `0x4010`
  * 4 bytes from `0x4011` to `0x4014`
  * 3 bytes from `0x4019` to `0x401B`
  * 14 bytes from `0x401C` to `0x4029`
  * 2 bytes from `0x40F9` to `0x40FA`
* The program memory is at `0x0800` and uses words of 5 bytes. The parameter memory is at `0x0000`
  and uses words of 4 bytes. This is the SigmaDSP memory design.
* The last two operations write `0x00` and then `0x03` to register `0x4036`. This is the
  dejitter register, and this is the last step of the ADI initialization sequence.

The registers `0x4008`, `0x400A` to `0x400D`, `0x401C` to `0x4029`, `0x4031` and `0x4036` are
different in the other parts of this family. The MV7 writes all of them. Therefore the part is the
ADAU1761 and not the ADAU1781 or the ADAU1381.

### 5.2 The memory map

| Range | Word size | Contents |
|---|---|---|
| `0x0000` to `0x07FF` | 4 bytes | Parameter memory. The words are 28 bits in 5.23 format. |
| `0x0800` to `0x0FFF` | 5 bytes | Program memory. The `getDSP` command rejects this range. |
| `0x1000` to `0x3FFF` | 4 bytes | More data memory. The firmware writes 2 words at `0x1FFE`. |
| `0x4000` to `0x40FA` | 1 byte | Control registers |

The `getDSP` command rejects the range `0x0800` to `0x0FFF` with `ErrA`. Therefore you cannot read
the program memory from the console. The other three ranges read correctly.

A read of the first words shows a gain table. The step between the words is `0x41893`:

```
0000: 00000FFE 00000000 00000000 00000000
000C: 00000000 00000000 0F800000 00400000
0010: 00041893 00041893 00083127 000C49BA
0014: 00108312 00149BA6 0018B439 001CCCCD
```

In 5.23 format the value `0x00400000` is 0.5.

### 5.3 The image format

The files `MV7DSP44K.hex` and `MV7DSP48K.hex` are lists of I2C write operations. Each file has
this structure:

```
u16[]   record lengths, big-endian, with a 0x0000 value at the end
record  addr_hi addr_lo data...      (the length comes from the table)
...
u32     version, big-endian (0x00000516 = 0.0.5.22)
```

The 44.1 kHz file has 35 records in 8387 bytes. The sequence is the standard ADI sequence:

1. Set `DSP_RUN` (`0x40F6`) to 0, which stops the DSP core.
2. Set the clock and the PLL (`0x4000`, `0x4002`).
3. Set the record path, the playback path and the serial ports.
4. Write 4220 bytes to `0x0800`, which is 844 program words of 5 bytes.
5. Write 3932 bytes to `0x0000`, which is 983 parameter words of 4 bytes.
6. Write 8 bytes to `0x1FFE`.
7. Set `DSP_RUN` to 1, which starts the DSP core.
8. Write `0x00` and then `0x03` to the dejitter register `0x4036`.

The file `tools/dsp_decode.py` reads these files and gives the register names.

### 5.4 The difference between the two sample rates

I compared the 44.1 kHz file with the 48 kHz file:

| Item | Result |
|---|---|
| The 75 control register values | The same |
| The program memory, 4220 bytes | The same in each byte |
| The parameter memory, 3932 bytes | 71.2 % of the bytes are the same |
| The 8 bytes at `0x1FFE` | The same |

Therefore the two files hold one DSP program with two sets of coefficients. Only the filter values
change with the sample rate. The MCU loads the correct file when the sample rate changes.

### 5.5 The register values

The file `data/dsp_programmed_regs.json` holds all 75 register values. These are the important
ones for the 44.1 kHz file. The values are the same for 48 kHz.

| Register | Name | Value |
|---|---|---|
| `0x4000` | CLOCK_CONTROL | `0x0F` |
| `0x4002` | PLL_CONTROL, 6 bytes | `00 01 00 00 20 03` |
| `0x4009` | REC_POWER_MGMT | `0x74` |
| `0x400A` to `0x400D` | REC_MIXER LEFT0/LEFT1/RIGHT0/RIGHT1 | `01 08 01 08` |
| `0x400E`, `0x400F` | LEFT and RIGHT DIFF_INPUT_VOL | `0x77` |
| `0x4010` | MICBIAS | `0x0D` |
| `0x4011` to `0x4014` | ALC_CTRL0 to ALC_CTRL3 | `00 4B 29 0D` |
| `0x4015`, `0x4016` | SERIAL_PORT0, SERIAL_PORT1 | `01 00` |
| `0x4019` | ADC_CONTROL | `0x33` |
| `0x4023`, `0x4024` | PLAY_HP_LEFT_VOL, PLAY_HP_RIGHT_VOL | `0xCF` |
| `0x4029` | PLAY_POWER_MGMT | `0x03` |
| `0x402A` | DAC_CONTROL0 | `0x03` |
| `0x4031` | JACK_DETECT_PIN | `0x08` |
| `0x40EB` | DSP_SAMPLING_RATE | `0x7F`, then `0x71` at the end |
| `0x40F5` | DSP_ENABLE | `0x01` |
| `0x40F6` | DSP_RUN | `0x00`, then `0x01` at the end |
| `0x40F9`, `0x40FA` | CLK_ENABLE0, CLK_ENABLE1 | `7F 01` |
| `0x4036` | DEJITTER | `0x00`, then `0x03` |

I also read the DSP memory from the connected microphone with the `getDSP` command. This document
does not give that data, because it is the firmware of the product. These are the quantities:

* all 251 control registers from `0x4000` to `0x40FA`
* 2048 parameter words from `0x0000` to `0x07FF`, of which 1916 are not zero
* 12288 data words from `0x1000` to `0x3FFF`, of which 4134 are not zero

Only four registers on the microphone are different from the values in the image. Each difference
has a cause:

| Register | Image | Device | Cause |
|---|---|---|---|
| `0x4023`, `0x4024` | `0xCF` | `0xC7` | The `volume` command changes these. See below. |
| `0x4028` | `0x02` | `0x1A` | POP_CLICK_SUPPRESS changes while the device operates. |
| `0x40EB` | `0x71` | `0x01` | DSP_SAMPLING_RATE. The device was at 48 kHz. |

### 5.6 The `volume` command and the codec

A test showed that the `volume` command writes to the two headphone volume registers of the
ADAU1761. I set four values and read the registers after each one:

| Command | `0x4023` and `0x4024` | Volume field, bits 7 to 2 |
|---|---|---|
| `volume 0` | `0xFF` | 63 |
| `volume -5` | `0xC7` | 49 |
| `volume -12` | `0x7F` | 31 |
| `volume -24` | `0x03` | 0 |

The two low bits are always `0b11`, which selects headphone mode and removes the mute. The value
of the field is `floor((N + 24) * 63 / 24)` for a command value of N dB.

Therefore the dB numbers of the `volume` command are not the dB numbers of the codec. The command
maps its range of −24 dB to 0 dB onto the full 6-bit range of the register.

### 5.7 The audio functions

The file `DSP-Settings-MV7.xml` connects each control in the user interface to a block ID and to
the DSP data. It gives the audio functions of the product:

* Compressor: Off, Light, Medium or Heavy
* Limiter: Off or On
* SM7 EQ: Off, HighPass, Presence, or both. This is a model of the SM7B tone switches.
* De-esser: on or off, with three settings
* Bass tame: on or off, with two settings
* ALC (automatic level): Off, or maximum gain 18, 24 or 30 dB
* Dynamics mode EQ: Off, or Close or Far with Neutral, Dark or Bright
* Monitor mix: 27 positions between the microphone and the playback signal
* DSP bypass

The `dspMode` command selects one of the seven `MODEn.dat` files. Each file is a set of these
values.

The MCU controls the DSP power with a pin that has the name `VA_EN_n`. The `dspPower` command sets
that pin. The `bootDSP` command starts the DSP again. The device then sends `dspBooted` or
`bootFailed`.

### 5.8 The block descriptor table

The `getBlock` and `setBlock` commands use a table in the EEPROM at `0x0200`. The file
`BLOCKDATA.dat` holds the same data. I decoded the format completely:

```
record: blockID(1) flag(1) kind(1)  then one or two entries
entry:  addr_hi(1) addr_lo(1) len(1) data[len]
```

A `kind` value of `0x0A` gives one entry. A value of `0x11` gives two entries. An address below
`0x4000` is parameter memory. An address of `0x4000` or more is a control register. The data from
all entries together is the exact reply of the `getBlock` command.

The table gives the DSP address of each control in the user interface:

| ID | Name (from `DSP-Settings-MV7.xml`) | ADAU1761 address |
|---|---|---|
| `0x19` | `Compressor_Select` | parameter `0x0008` |
| `0x1F` | `Limiter_Select` | parameter `0x0009` |
| `0x31` | `SM7_EQ_Select` | parameter `0x000A` |
| `0x33` | `Bass_Tame_Select` | parameter `0x000B` |
| `0x32` | `Deess_Select` | parameter `0x000C` |
| `0x34` | `Dynamics_Mode_EQ_Select` | parameter `0x000D` |
| `0x35` | (no name in the XML) | parameter `0x003E` |
| `0x03` | `BassTame_OnOff` | parameters `0x016E` and `0x016F` |
| `0x02` | `Deess_OnOff` | parameters `0x02E0` and `0x02E1` |
| `0x99` | `DSP_Bypass` | parameters `0x03D1` and `0x03D2` |
| `0x22` | `Monitor_Mix` | parameters `0x03D4` and `0x03D3` |
| `0x21` | `ALC` | **register** `0x4011`, which is `ALC_CTRL0` |

Three tests agree with this decode. The `getBlock 2` command gave `0000000000800000`, which is the
same as parameters `0x02E0` and `0x02E1` in the table. The `getBlock 19` command gave `00000000`,
which is the same as parameter `0x0008`. The `Monitor_Mix` value `00198a13004026e7` is option 12
in the XML file, with the name "Mic 13".

The tool `tools/blockmap.py` reads this table.

### 5.9 The program memory

Analog Devices does not publish the SigmaDSP instruction set. No public disassembler exists. The
community work on SigmaDSP (the AidaDSP group) covers the SigmaStudio blocks, not the instructions.

To find the encoding I used a reference set of nine programs from the `MCUdude/SigmaDSP` project.
Each one has the SigmaStudio block diagram, the compiled program memory, and a file that gives a
name and an address to each parameter. The programs increase in size from 8 words to 231 words.
They are for the ADAU1701, which uses the same SigmaDSP core generation as the ADAU1761.

**The word layout:**

| Bits | Name | Function |
|---|---|---|
| `[39:27]` | A | Data or register address, 13 bits. The top of the range is the I/O area. |
| `[26:16]` | P | Parameter address. 8 bits on the ADAU1701, 10 bits on the ADAU1761. |
| `[15:0]` | OP | A group of control bits. It is not a number that selects one operation. |

**Two opcode bits are now known:**

| Bit | Value | Function |
|---|---|---|
| 13 | `0x2000` | Use the multiplier |
| 9 | `0x0200` | Add to the sum. Without this bit the instruction starts a new sum. |
| 0 | `0x0001` | Set on each ADAU1701 instruction, clear on each ADAU1761 instruction. This is a difference between the parts, not a function. |

The evidence is the second-order EQ reference. Its ground truth is 4 filter stages on 2 channels.
All 8 filters are one `0x2001` instruction and then four `0x2201` instructions. Those two values
are different at bit 9 only. A biquad filter is five multiply operations added together. Therefore
the first instruction starts the sum and the other four add to it.

**Result: the MV7 contains 48 biquad filters.** A search for one start instruction and four add
instructions finds 48 groups of 5 in the MV7 program. Three independent facts agree:

* All 48 groups use coefficients that are different between the 44.1 kHz and the 48 kHz image.
  Therefore all of them are filter coefficients.
* The 48 groups use exactly 240 parameters, which is 48 multiplied by 5.
* The parameters are in the three banks at 370 to 449, 452 to 531 and 534 to 614. Each bank holds
  exactly 16 complete filters. Section 5.4 found these three banks from the parameter data alone.

The program also has 18 groups of 2 and one group of 3. These are smaller filters or mixers.

The order of the five coefficients is different in the two parts:

| Part | Order |
|---|---|
| ADAU1701 | base+3, base+4, base+0, base+1, base+2 |
| ADAU1761 | base+2, base+4, base+0, base+1, base+3 |

The five instructions of one filter read data addresses that decrease by 1. Example: `0x00B0`,
`0x00AF`, `0x00AE`, `0x00AD`, `0x00AC`. This is the delay line of the filter.

**Four instruction classes are now identified.** The evidence for each:

| Class | Opcode | Evidence |
|---|---|---|
| `mac.start` | `0x2000` | The first tap of each filter. It starts a new sum. |
| `mac.acc` | `0x2200` | Taps 2 to 5. Different from `0x2000` at bit 9 only. |
| `store.dm` | `0xE200` | It comes after 122 of the 162 multiply sequences. Its address is one more than the address of the first tap in 53 of them. The next filter then reads that address. |
| `store.out` | `0x0200` | The same position in the reference template, with an address in the output area. |
| `io` | `0x0000` | 59 instructions with an address of 8154 or more, which is the I/O area. They are at the start of the program. |

The cascade of filters makes the `store.dm` result clear. Filter 1 reads the addresses `0x00B0`
down to `0x00AC` and stores to `0x00B1`. Filter 2 then reads `0x00B3` down to `0x00AF`, which
includes `0x00B1`. Each filter therefore writes its result into the delay line of the next filter.

With these five classes the tool identifies **82 % of the 806 MV7 instructions** and 87 % of the
instructions in the reference programs.

**Validation on independent programs.** I collected 58 more SigmaStudio exports from 43 public
projects. The ADAU176x programs in that set use the same opcode values as the MV7 (`0x2000`,
`0x2200`, `0xE200`, `0xC000`, `0xF200`, `0x3400`, `0x2240`) and the same filler word. Therefore
the encoding is not specific to Shure.

I then applied the classifier to 28 programs that I did not use to build it:

| Family | Programs | Mean coverage |
|---|---|---|
| ADAU176x | 4 | 75.2 % |
| ADAU1701 | 22 | 67.4 % |
| ADAU1452 | 2 | 1.1 % |

The ADAU1452 result is a control. That part is a later SigmaDSP generation with a different word
format. A classifier that matched random data gives a similar result for all three families. This
classifier does not.

**The parameter field, tested again.** The reference programs gave a different answer at first,
which was an error in the test. Their data addresses and their parameter addresses are both in the
range 0 to 92, so any address field appears correct. The MV7 gives a test that has no such
problem. Only the coefficients of a filter change between the 44.1 kHz image and the 48 kHz image:

| Candidate field | Chains with all 5 coefficients rate-dependent | Distinct coefficients |
|---|---|---|
| `bits[25:16]` | **48 of 48** | **240 of 240** |
| `bits[37:28]` | 30 of 48 | 84 of 240 |
| `bits[34:28]` | 4 of 48 | 84 of 240 |
| `bits[32:27]` | 0 of 48 | 64 of 240 |

Only `bits[25:16]` gives 240 different coefficients for 240 positions. The others put many filters
on the same coefficients, which is not possible. The field is `bits[25:16]`.

**The other opcode bits are not known.** The graded reference series gives this attribution. The
list shows the first program that needs each bit:

| First use | New opcode bits |
|---|---|
| Bare input to output path | 0, 9, 11, 13, 14, 15 |
| Slewed volume control | 6, 10, 12 |
| State-variable filter | 1, 2, 3, 5, 7 |
| Oscillator | 8 |
| Not used in any reference program | 4 |

The first-order EQ and the second-order EQ add no new opcode. A filter uses the same instructions
as a volume control.

A larger set of programs does not give better attribution. I tested 28 programs against the module
names in their parameter files. The strongest association was only 3.1 times the base rate,
because almost every real project contains the same mix of volume, filter and gain blocks. To give
a name to more opcode bits you need programs that each contain one block only. SigmaStudio must
make those programs.

The tool `tools/sigmadsp_disasm.py` decodes both parts and marks each filter. It gives the correct
result on the reference programs. Run it on a firmware package to make the listing of all 844 MV7
words. The file `data/adau1761_analysis.md` gives the results with no program data.

### 5.10 Not known

I did not identify these items:

* The function of the 35 opcodes
* The function of the 2 words at `0x1FFE`
* The meaning of each bit in the 75 register values
* The purpose of parameter `0x003E`, which is block `0x35`

## 6. The update process

The file `UpdateInstructions.xml` controls the host software:

```xml
<UpdateOperation><Name>UploadCommand</Name><Arg>PRIMARY</Arg></UpdateOperation>
<UpdateOperation><Name>Copy</Name><Arg>MV7.embeddedManifest.hex</Arg><Arg>0x00</Arg></UpdateOperation>
<UpdateOperation><Name>Copy</Name><Arg>MonoIn_StereoOut_24b_96KHz_1.cyacd</Arg>...</UpdateOperation>
<UpdateOperation><Name>Copy</Name><Arg>MonoIn_StereoOut_24b_96KHz_2.cyacd</Arg>...</UpdateOperation>
<UpdateOperation><Name>Copy</Name><Arg>DSP-MV7.dat</Arg>...</UpdateOperation>
<UpdateOperation><Name>Copy</Name><Arg>DSP-Settings-MV7.xml</Arg>...</UpdateOperation>
<UpdateOperation><Name>Execute</Name><Arg>PRIMARY</Arg><Arg>1.2.19</Arg></UpdateOperation>
```

There are two different paths. The paths do not share code.

### 6.1 The EEPROM path for the DSP and the presets

This path uses the `update` command on the console. The 11 names in the file table at `0x1a60c`
are the only permitted files. **No `.cyacd` file is in that table.** Therefore this path cannot
write to the MCU flash.

The handler at `0x0000b260` gives these safety properties:

1. The device writes to the file slot that is not in use. The function at `0x0000af24` calculates
   that address from the file number and the other slot number.
2. The device writes 64 bytes at a time. After each write it reads the data back and compares it
   with the RAM buffer. If the comparison fails, the upload stops.
3. The device calculates the checksum from the data that it read back, not from the data that the
   host sent. Therefore the checksum shows what is in the memory.
4. The device counts the bytes. If the data is longer than the `<0xLEN>` value, the upload stops.
5. The `END <0xCRC>` value must agree with the calculated checksum. If it does not agree, the
   device does not mark the file as good.
6. The device writes the slot number of each good file to a staging area at `0xAE0`.
7. After the last file (number 10), the device writes all 11 slot numbers to the active area at
   `0xAC0` in one operation. Then it clears the staging area and sets the flag at `0xABF` to
   `0x15`.
8. The `pkgVersion` command writes the new version text only when the flag at `0xABF` is `0x15`.
9. If no data arrives for 2000 ms, the console leaves the raw mode and writes nothing.

The result is a two-phase commit. If an upload stops for any reason, the old files stay active and
the old version text stays correct. You can then start the upload again.

Two conditions are still a risk on this path:

* The commit at step 7 is one write of 11 bytes. If the power fails during that write, some files
  can point to the new slot and some to the old slot. The write is small and one EEPROM page
  operation usually completes or does not start. Therefore this risk is small.
* The `update` command for file 0 clears 64 bytes of the active area at `0xAC0` if the current
  slot number is not 1 or 2. This is the recovery path for a device with no good file set. On a
  good device the code does not use this path.

### 6.2 The flash path for the MCU

This path uses the `bootLoad` command. That command writes the value `0x12345678` to RAM address
`0x200077F8`. Then it resets the MCU. The bootloader reads that value and does not start the
application.

The value is in SRAM. A power cycle erases SRAM. If the host stops before it writes any flash,
remove the USB cable and connect it again. The application then starts.

**WARNING: Do not write to the application slot that is in use.** If you erase that slot and the
host then stops, the microphone has no good application. The bootloader stays active until a host
completes the update.

I put the microphone in the bootloader and sent only the commands that read. These are the
results. I wrote no flash.

| Item | Result |
|---|---|
| USB ID | `14ed:1012`, the same as the application. Only the product text changes, to `PSoC4 Bootloader`. |
| USB class | HID, one interface, endpoints `0x01` OUT and `0x82` IN, 64 bytes |
| Device node | The same `hidraw` node as the console |
| `bcdDevice` | 0.04. The serial text is `0001`. |
| Bootloader version | 1.1.60 |
| Silicon ID | `0x100F11A0`, which is the same as the `.cyacd` header |
| Security key | **None.** `ENTER` with an empty data field gives status `0x00`. |
| Writable rows | 43 to 1023. Rows 0 to 42 hold the bootloader and are protected. |
| Active application | Application 0, which is slot 1 |
| Exit | The `EXIT` command (`0x3B`) starts the application again. A power cycle is not necessary. |

The same USB ID for the application and the bootloader explains an earlier result. A search of
`ShureDeviceManager.exe` for a second product ID found nothing, because there is no second one.

The checksum of the bootloader protocol is a reflected CRC-16 with the polynomial `0x8408`. The
result is inverted, then the two bytes change position, then the value goes on the wire with the
low byte first. I tested four possible methods. Only this one gives status `0x00`.

### 6.3 A test of the MCU update

I updated the microphone from package 1.2.17 to the firmware in package 1.2.19. The sequence was:

1. The `bootLoad` command put the device in the bootloader.
2. `ENTER` gave silicon ID `0x100F11A0`, which is the same as the `.cyacd` header.
3. `APP_STATUS` showed that application 0, which is slot 1, was the running application.
4. The tool wrote the 410 rows of slot 2, which is application 1, in 11 seconds.
5. `VERIFY_CHECKSUM` gave a good result.
6. `SET_ACTIVE_APP` made application 1 active. Then `EXIT` started it.

The result: `fwVersion` changed from `0.0.49.0` to `0.0.52.0`. The settings did not change. A
comparison of the EEPROM before and after the update showed that no byte changed.

This order is safe. The tool writes only the slot that is not running, and it makes the new slot
active only after the bootloader gives a good checksum. A failure before that point leaves the old
image as the running image.

Immediately after the flash the `pkgVersion` command gave `1.2.17*`. The star shows that the
package text does not agree with the components. The `pkgVersion` command refuses to write while
the flag at EEPROM `0xABF` is not `0x15`, and only a complete DSP upload sets that flag.

Therefore I ran the DSP upload for all 11 files. Each file gave a good `END` checksum. The upload
needed 417 seconds. Then `pkgVersion 1.2.19 0.0.52.0 0.0.5.22` was accepted.

The EEPROM data shows the two-phase commit:

```
active slot for each file, 0xAC0..0xACA
   before: 01 01 01 01 01 01 01 01 01 01 01
   after : 02 02 02 02 02 02 02 02 02 02 02
```

All 11 files moved to the other slot in one operation. The `fileLoc` command now gives
`0x8600` for `MV7DSP44K.hex`, in place of `0x0C00`. The old copies stay in the memory. The flag at
`0xABF` is `0x15` after the update, and the `pkgVersion` command does not clear it.

The microphone now reports `pkgVersion=1.2.19`, `fwVersion=0.0.52.0` and `dspVersion=0.0.5.22`.

The file `MV7.embeddedManifest.hex` goes to EEPROM offset 0. It records the component versions:

```
!010213000080
"A00034C1400003400        A = ..._1.cyacd,  version 0.0.52.0
"B00034C1400003400        B = ..._2.cyacd,  version 0.0.52.0
"D000097BF00000516        D = DSP-MV7.dat,       0.0.5.22
"E00000FD900000516        E = DSP-Settings-MV7,  0.0.5.22
#04F37A8C-FD5A-11E3-ACBA-0015C5F3F612     DCID
```

## 7. The host software

```
MOTIV Mix (Electron and Angular)
  └─ @shure/application-apollo-link  ── GraphQL and Apollo
      └─ @shure/system-api-*         ── device model, resolvers, update policy
          └─ gRPC ──► ShureDeviceManager.exe ("Phoenix")
                        ├─ PHX::PeriodicServerManifestDownloader  (Updates.xml)
                        ├─ PHX::RemotePackageStore                (.pack files)
                        ├─ MV7CliDevice and MV7CliMsgTranslator_0_3_0
                        └─ USB HID ──► MV7
```

The device reports its `interfaceId`. The host uses that value to select one of the four
translators `MV7CliMsgTranslator_0_0_1`, `_0_1_0`, `_0_2_0` and `_0_3_0`. If the value is unknown,
the host writes `CliMsgTranslatorFactory unsupported MV7 interfaceID`. The name "Cli" in all of
these classes shows that Shure uses this console as the control protocol of the product.

The three files `DDL.dat`, `FD.dat` and `DCIDMap.dat` are ZIP files with AES encryption. Four
possible passwords are near the `DCIDMap.dat` text in `ShureDeviceManager.exe`. They are
Therefore the software builds the key while it runs. The MV7 analysis did not need these files.
