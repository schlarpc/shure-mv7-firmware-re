# ADAU1761 program analysis: findings


These are the results of the analysis of the MV7 DSP program. This document gives no
program word and no coefficient value. Use `tools/sigmadsp_disasm.py` on a firmware
package to make the full listing. Section 5 of `firmware.md` gives the method.

## Program statistics

| Item | Value |
|---|---|
| Program words | 844 |
| Words that are not the filler | 806 |
| Different opcodes | 37 |
| Parameter words in the image | 983 |
| Parameters that change with the sample rate | 425 |
| Parameters that an instruction addresses directly | 360 |

## Instruction classes

| Class | Count | Part of the program |
|---|---|---|
| `mac.start` | 162 | 20 % |
| `mac.acc` | 254 | 32 % |
| `store.dm` | 169 | 21 % |
| `store.out` | 13 | 2 % |
| `io` | 59 | 7 % |
| `unknown` | 149 | 18 % |

The five identified classes give 82 % of the instructions.

## Opcodes, by how often they occur

| Opcode | Count | Class |
|---|---|---|
| `0x2200` | 254 | mac.acc |
| `0xE200` | 169 | store.dm |
| `0x2000` | 162 | mac.start |
| `0x0000` | 63 | not known |
| `0xC000` | 23 | not known |
| `0xF200` | 17 | not known |
| `0x0200` | 13 | store.out |
| `0x2240` | 11 | not known |
| `0x3400` | 11 | not known |
| `0x2008` | 10 | not known |
| `0x2100` | 9 | not known |
| `0xE227` | 8 | not known |
| `0xA100` | 6 | not known |
| `0x2080` | 5 | not known |
| `0xA000` | 4 | not known |
| `0x4200` | 4 | not known |

## Multiply sequences

| Length in taps | Count | Meaning |
|---|---|---|
| 1 | 95 | one multiply, a gain |
| 2 | 18 | a first-order section |
| 3 | 1 | — |
| 5 | 48 | a biquad filter |

## The 48 biquad filters

Each one is five instructions. All of their coefficients change with the sample rate.
The table gives the position in the program and the first coefficient of each filter.
It gives no coefficient value.

| Bank | Coefficient parameters | Filters | Program positions |
|---|---|---|---|
| A | 370 to 449 | 16 | 0x11A to 0x17A |
| B | 452 to 531 | 16 | 0x183 to 0x1E3 |
| C | 534 to 614 | 16 | 0x1EC to 0x24C |

The coefficient order in each filter is base+2, base+4, base+0, base+1, base+3.
The five instructions read data addresses that decrease by 1, which is the delay line.
The instruction after each filter is a `store.dm` to the address one above the first tap.

## Control registers

I read all 251 control registers from `0x4000` to `0x40FA` on the device. Only four were
different from the values in the firmware image, and each difference has a cause. The
table is in section 5.5 of `firmware.md`. The full set of values is in
`dsp_programmed_regs.json`.

