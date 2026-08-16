# The MV7 vendor HID protocol


I tested all of the data in this document on a connected microphone. That microphone had package
version 1.2.17 and reported `interfaceId=0.3.0`. Text with the words "from the code" comes from
the firmware image only. I did not test it on the device.

## 1. Transport

The MV7 is a USB 2.0 full-speed composite device with the ID `14ed:1012`. It has five interfaces.

| Interface | Class | Function |
|----|-------|---------|
| 0 | Audio, Control | UAC1 control: mute and volume units, headphone and microphone terminals |
| 1 | Audio, Streaming | Host to device (headphone), 2 channels, 16 or 24 bits, 44.1 or 48 kHz. Endpoints `0x01` OUT and `0x88`. |
| 2 | Audio, Streaming | Device to host (microphone), 1 channel, 16 or 24 bits, 44.1 or 48 kHz. Endpoint `0x82` IN. |
| **3** | **HID, vendor** | **The command console.** Endpoints `0x84` IN and `0x05` OUT, 64 bytes, interval 3 ms. |
| 4 | HID, consumer | The consumer control page for the mute key |

The report descriptor of interface 3 is 38 bytes. It gives one input report of 64 bytes and one
output report of 64 bytes. Both reports are on usage page `0xFF00`. Neither report has a report ID.

```
06 00 FF     Usage Page (Vendor FF00)
09 01        Usage (1)
A1 01        Collection (Application)
19 01 29 40    Usage Minimum(1) to Maximum(0x40)
15 00 26 FF 00 Logical 0 to 255
75 08 95 40    Report Size 8, Report Count 64
91 02          Output (Data,Var,Abs)
19 00 29 40    Usage Minimum(0) to Maximum(0x40)
15 00 26 FF 00
75 08 95 40
81 02          Input (Data,Var,Abs)
C0
```

The protocol has no message frames. Write ASCII text into the 64-byte OUT report. Then read ASCII
text from the IN report. The unused bytes at the end of a report are `0x00`.

## 2. How the device assembles a command line

One report is not one command. The device puts the data from each OUT report into a **128-byte
line buffer**. It runs the command only when it finds a NUL byte. If a report has no NUL byte in
its 64 bytes, the line stays open. The device then adds the next report to the same line.

A test on the device showed this behavior. I sent 64 characters `A` with no NUL byte. The device
gave no reply. It then joined the next command to the open line:

```
send: 'A' x 64          -> (no reply)
send: 'fwVersion'       -> No such command: AAAA...AAAfwVersion
```

Obey these two rules in a client:

1. Put a NUL byte at the end of each command.
2. If a command is longer than 63 characters, divide it between two or more reports.

A line of 128 characters or more is too long. The device replies `Data Full \r\nCLI:`. The maximum
length of a command is therefore **127 characters**.

The tokenizer divides the line at each space and each tab. Therefore a `\r` or `\n` byte at the
end causes no error. The commands `help`, `help\r`, `help\n` and `help\r\n` all give the same
result.

## 3. Replies

A reply is one or more 64-byte IN reports. Each report holds NUL-terminated ASCII text. Each line
of text ends with `\n`. A reply longer than 63 bytes goes into two reports. The maximum length of
one reply is 125 bytes.

| Reply | Meaning |
|---|---|
| `name=value` | The value of a parameter |
| `Recognized commands:` | The start of the command list |
| `[Failed]` | The handler returned an error |
| `Not valid` | The device rejected the argument |
| `Invalid CMD` | The device received the wrong number of arguments |
| `ErrA` | Bad address |
| `ErrC` | Bad count |
| `ErrS` | Bad syntax |
| `ErrD` | Bad data |
| `ErrE` | EEPROM error |
| `CMD failed` | An upload operation failed |
| `Data Full \r\nCLI:` | The command line was too long |

The protocol has no end-of-reply marker. Read from the endpoint until it becomes quiet.

## 4. Privilege levels

The dispatcher is at address `0x0000a4cc`. It reads a table of 48 entries. Each entry has this
structure:

```c
struct cmd {
    const char *name;
    int (*handler)(int argc, char **argv);
    const char *args;
    const char *help;
    uint32_t    level;
};
```

The dispatcher runs a handler only when `entry->level <= state->level`. A table at address
`0x1aa18` gives the four level names.

| Value | Name | Meaning |
|---|---|---|
| 0 | `nor` | Normal |
| 1 | `dev` | — |
| 2 | `adm` | **The level after power-on** |
| 3 | `sup` | Superuser or factory |

The command `su` with no argument gives the level of the console. The command `su <name>` sets the
level. This is the complete mechanism. This code comes from the handler:

```c
/* cmd_su, 0x0000a72c */
for (i = 0; i < 4; i++)
    if (strcmp(levelNames[i], argv[1]) == 0) { level = i; break; }
if (i == 4) level = 0;
state->level = level;
```

The console has no password, no nonce, no challenge and no limit on the number of attempts. A test
on the device gave these results:

```
$ su          su=adm      the level after power-on
$ su ?        su=nor      an unknown name gives level 0
$ su sup      su=sup      the highest level, immediately
```

The device reads the level after power-on from **EEPROM byte `0x8D`**. The function at
`0x00004f18` reads this byte. If the byte is `0x10` or more, the function writes a correct value.
The `setEE` command can change this byte.

The `help` and `?` commands show only the commands at the level of the console or lower. The list
stops at the first entry with no help text. Therefore the list does not show these five commands:
`?`, `help`, `su`, `logDebug` and `update`. All five commands operate correctly.

## 5. The commands

The command table is at address `0x000198b4`. Each entry is 20 bytes. There are 48 entries.

| No. | Command | Arguments | Description | Level |
|---|---|---|---|---|
| 0 | `audioMute` | `[on\|off]` | Get or set the audio mute | 0 `nor` |
| 1 | `micMute` | `[on\|off]` | Get or set the microphone mute | 0 `nor` |
| 2 | `volume` | `[-24...0]\|up\|down` | Get or set the speaker volume in dB | 0 `nor` |
| 3 | `inputGain` | `[0...+36]` | Get or set the recording gain in dB | 0 `nor` |
| 4 | `fwVersion` | — | Get the firmware version | 0 `nor` |
| 5 | `serialNum` | — | Get the serial number of the silicon | 1 `dev` |
| 6 | `dspMode` | `[1...7]` | Get or set the DSP mode number | 1 `dev` |
| 7 | `dspVersion` | — | Get the DSP version | 0 `nor` |
| 8 | `bootDSP` | `[C]` | Start the DSP again with the default data | 1 `dev` |
| 9 | `getSampleRate` | `[in\|out]` | Get the sample rate and the bit depth | 1 `dev` |
| 10 | `getBlock` | `blockId` | Read a DSP parameter block | 2 `adm` |
| 11 | `setBlock` | `blockId dataString` | Write a DSP parameter block | 2 `adm` |
| 12 | `getEE` | `addr [count]` | Read the EEPROM | 3 `sup` |
| 13 | `setEE` | `addr value` | Write to the EEPROM | 3 `sup` |
| 14 | `bootLoad` | — | Start the bootloader | 3 `sup` |
| 15 | `pkgVersion` | `[pkgVer fwVer dspVer]` | Get or set the package version | 0 `nor` |
| 16 | `lock` | `[on\|off]` | Lock or unlock the user configuration | 2 `adm` |
| 17 | `myCID` | — | Get the CID text of the device | 1 `dev` |
| 18 | `dimMode` | `[on\|off]` | Get or set the dim mode of the LEDs | 2 `adm` |
| 19 | `meterMode` | `[on\|off]` | Get or set the audio meter mode | 2 `adm` |
| 20 | `rateMismatch` | — | Get the sample rate mismatch condition | 1 `dev` |
| 21 | `deviceType` | — | Get the Shure device category (DCID) | 2 `adm` |
| 22 | `interfaceId` | — | Get the Shure device interface ID | 2 `adm` |
| 23 | `identify` | — | Flash the LEDs of the device for two seconds | 0 `nor` |
| 24 | `disableUI` | `[on\|off]` | Disable or enable the touch panel | 3 `sup` |
| 25 | `hwMute` | `[on\|off]` | Mute the analog headphone output | 3 `sup` |
| 26 | `dspPower` | `[on\|off]` | Set the DSP power pin `VA_EN_n` | 3 `sup` |
| 27 | `getDSP` | `startAddr [endAddr]` | Read the DSP memory | 3 `sup` |
| 28 | `setDSP` | `addr value` | Write to the DSP memory | 3 `sup` |
| 29 | `ledTest` | `[on\|off]` | Set the LED test mode | 3 `sup` |
| 30 | `buttonTest` | `[on\|off]` | Set the button test mode | 3 `sup` |
| 31 | `getLed` | `ChipN RegN` | Read an LED driver register | 3 `sup` |
| 32 | `setLed` | `ChipN RegN value` | Write to an LED driver register | 3 `sup` |
| 33 | `myLed` | `ledId` | Get the condition of an LED | 3 `sup` |
| 34 | `turnLed` | `ledId on/off` | Set an LED on or off | 3 `sup` |
| 35 | `flashLed` | `ledId on/off durationMs onTime offTime` | Flash an LED | 3 `sup` |
| 36 | `brightness` | `ledId percent` | Set the brightness of an LED | 3 `sup` |
| 37 | `fileLoc` | `fileID` | Get the address, name and version of a DSP file | 3 `sup` |
| 38 | `getSN` | — | Get the board serial number | 1 `dev` |
| 39 | `setSN` | `Board number String(26)` | Set the board serial number | 3 `sup` |
| 40 | `getSAPSN` | — | Get the Shure SAP number | 1 `dev` |
| 41 | `setSAPSN` | `Shure SAP number String(11)` | Set the Shure SAP number | 3 `sup` |
| 42 | `getApple` | `Dev` | Get the condition of the Apple MFi coprocessor | 3 `sup` |
| 43 | `?` | — | Not in the list. The same as `help`. | 0 `nor` |
| 44 | `help` | — | Not in the list. Shows the commands. | 0 `nor` |
| 45 | `su` | — | Not in the list. Gets or sets the privilege level. | 0 `nor` |
| 46 | `logDebug` | — | Not in the list. Debug log control. | 3 `sup` |
| 47 | `update` | — | Not in the list. Starts a file upload. See section 8. | 0 `nor` |

### Data about some commands

The `volume` command accepts a value from −24 dB to 0 dB. It also accepts the words `up` and
`down`, which change the volume by 2 dB. A test gave `volume=-3dB` after `volume up` and
`volume=-5dB` after `volume down`.

The `inputGain` command accepts a value from 0 dB to +36 dB, in steps of 1 dB.

The `dspMode` command selects one of the seven `MODEn.dat` presets in the EEPROM. If the `lock`
mode is on, the device replies `Locked` and makes no change.

The `getSampleRate` command with no argument gives all three values. With the argument `in` or
`out` it gives one value.

The `getEE` command takes a hexadecimal address from 0 to `0x1FFFF`. The count is decimal, from 1
to 256. With no count, the device reads one byte. The reply is a hexadecimal dump with a column
header. The device aligns each address down to a 16-byte boundary and shows the first cells as
`--`.

The `getDSP` command takes hexadecimal addresses. The permitted ranges are `0x0000` to `0x07FF`
and `0x1000` to `0x3FFF`, which the device shows as 32-bit words. The range `0x4000` to `0x40FA`
shows as bytes. The device rejects the range `0x0800` to `0x0FFF` with `ErrA`.

The `fileLoc` command takes a file number from 0 to 10. Section 5 of `firmware.md` gives the file
list.

The `identify` command replied `OK` on the device. The help text says that the command flashes the
LEDs for two seconds. I did not look at the microphone during the test.

**CAUTION: Do not send the `bootLoad` command unless you can complete a firmware update.**
The command writes the value `0x12345678` to RAM address `0x200077F8` and then resets the MCU.
The bootloader then starts instead of the application. Section 6 of `firmware.md` gives more data.

## 6. Asynchronous events

The device sends notifications on the IN endpoint without a request. These notifications use the
same `name=value` syntax as the replies. The function at `0x00004f74` sends them. This function
reads the command table for each name. Therefore the events and the commands use the same words.

This table comes from the code. I saw only the `volume` event on the device.

| Case | Format | Example |
|---|---|---|
| 0 | `Reset from %s` | `Reset from POWERON`, `WATCHDOG`, `SOFTWARE`, `PROTFAULT` |
| 1 | `USB %s` | `USB is connected`, `USB Disconnected` |
| 2 | `CurrentRate=%ld` | `CurrentRate=48000` |
| 3 | `outRate=%ld` | |
| 4 | `outDepth=%d` | |
| 5 | `inRate=%ld` | |
| 6 | `inDepth=%d` | |
| 0x0B | `volume=%sdB` | The user moved the touch strip |
| 0x0D | `audioMute=%s` | |
| 0x0F | `inputGain=%sdB` | |
| 0x11 | `micMute=%s` | The user touched the mute pad |
| 0x13 | `dspMode=%d` | |
| 0x14 | `fwVersion=%d.%d.%d.%d` | |
| 0x15, 0x16 | `serialNum=%s` | |
| 0x17 | `dspVersion=%d.%d.%d.%d` | |
| 0x18 | `dspBooted` | |
| 0x19 | `bootFailed` | |
| 0x1A | `lock=%s` | |
| 0x1B | `USB IN Token Missing` | USB fault data |
| 0x1C | `USB SOF Missing` | USB fault data |
| 0x1D | `rateMismatch=%s` | |

A test showed one of these events. The device reset itself during the tests in `security.md`.
After the device connected to the USB bus again, the queue held `volume=-5dB`.

This is the remote control interface. A host can set the gain, the volume, the mute condition, the
DSP preset and the LEDs. The device tells the host when the user makes a change. MOTIV Mix uses
this interface to keep its display correct.

### Button and slider events

The `buttonTest on` command needs level `sup`. In this mode the device sends more events. These
text strings are in the image at addresses `0x1a88a` to `0x1a93e`:

```
MUTE_BUTTON PRESSED / ON HOLD / RELEASED
PHONE_MIC_BUTTON PRESSED / ON HOLD / RELEASED
SLIDER_UP %s
SLIDER_DOWN %s
```

I did not see these events. A test needs a person to touch the panel while the mode is on.

## 7. Parameter blocks

The `getBlock <id>` and `setBlock <id> <hexstring>` commands read and write DSP parameter blocks.
The block IDs are the same as the IDs in `DSP-Settings-MV7.xml`. The `setBlock` command reads the
data as pairs of hexadecimal digits. It accepts a maximum of 64 bytes. It stops at the first
character below `'0'`.

A test read the IDs 0 to 19. Three blocks had data:

```
block 2  0000000000800000     Deess_OnOff        = Off
block 3  0000000000800000     BastTame_OnOff     = Off
block 19 00000000             Compressor_Select  = Off
```

The block IDs are hexadecimal. `DSP-Settings-MV7.xml` gives these names:

| ID | Name | Data |
|---|---|---|
| 0x02 | `Deess_OnOff` | 8 bytes |
| 0x03 | `BassTame_OnOff` | 8 bytes |
| 0x19 | `Compressor_Select` | 4 bytes: 0 Off, 1 Light, 2 Medium, 3 Heavy |
| 0x1F | `Limiter_Select` | 4 bytes: 0 Off, 1 On |
| 0x21 | `ALC` | 4 bytes: Off, or maximum gain 18, 24 or 30 dB |
| 0x22 | `Monitor_Mix` | 8 bytes, 27 blend positions |
| 0x31 | `SM7_EQ_Select` | 4 bytes: Off, HighPass, Presence, or both |
| 0x32 | `Deess_Select` | 4 bytes, 3 settings |
| 0x33 | `Bass_Tame_Select` | 4 bytes, 2 settings |
| 0x34 | `Dynamics_Mode_EQ_Select` | 4 bytes: Off, Close or Far, Neutral or Dark or Bright |
| 0x99 | `DSP_Bypass` | 8 bytes |

**CAUTION: Do not send a `getBlock` argument of 114 characters or more.** The command has a
memory fault. The MCU stops and resets. The file `security.md` gives the data.

## 8. File upload with the `update` command

The command `update <fileName> <0xLEN>` puts the console into a raw data mode. In this mode the
dispatcher sends each line to a different handler at address `0x0000b260`. The command table is
not used. If no line arrives for 2000 ms, the device leaves this mode.

The device compares `<fileName>` with a table of 11 names at address `0x1a60c`. Each entry in that
table is 28 bytes. The `<0xLEN>` value is the length of the file in bytes, in hexadecimal.

The file `DSP-MV7.dat` in the firmware package is a record of this protocol:

```
#DSP
#VER: 00.00.05.22
update MV7DSP44K.hex 0x20C3
00030003000300080004000400060003        16 bytes as 32 hexadecimal characters
...
END 0x5915                              a 16-bit summation checksum
update MV7DSP48K.hex 0x20C3
...
END 0xC4E8
update MODE1.dat 0x0081
...
```

The file has 11 `update` and `END` pairs. The device keeps the checksum of each file in the EEPROM
with the version. The `CAPABILITIES.dat` data at EEPROM address `0x53A0` is
`00 00 05 16 A8 E9`. The first four bytes are version 0.0.5.22. The last two bytes are `0xA8E9`,
which is the same as `END 0xA8E9` in the package. The EEPROM data from the device agrees with the
package.

The `update` command is at table level 0. Therefore the `su` command is not necessary before an
upload. Section 6 of `firmware.md` gives the safety properties of this protocol.
