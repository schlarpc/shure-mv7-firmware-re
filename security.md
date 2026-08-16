# MV7 security findings


I examined firmware `0.0.52.0` from package 1.2.19. I did the device tests on a microphone with
package 1.2.17 and firmware `0.0.49.0`.

Any program on the host computer can use the vendor HID interface. On Windows and on macOS, a
program does not need administrator rights to open a HID device. A web page can also use WebHID.
Therefore "a local program" in this document includes code that the user did not install.

---

## F1. The `su` command gives full privilege with no authentication

**Level: high. I did this test on the device.**

The console has four privilege levels. Level `sup` controls 21 of the 48 commands. The only test
is a comparison of text:

```c
/* cmd_su, 0x0000a72c - this is the complete authentication code */
for (i = 0; i < 4; i++)
    if (strcmp(levelNames[i], argv[1]) == 0) { level = i; break; }
if (i == 4) level = 0;
state->level = level;
```

The test on the device gave this result:

```
$ su          su=adm      the level after power-on
$ su sup      su=sup
$ help        ... getEE setEE bootLoad disableUI hwMute dspPower getDSP setDSP
                  ledTest buttonTest getLed setLed myLed turnLed flashLed
                  brightness fileLoc setSN setSAPSN getApple
```

There is no password, no challenge, no host authentication and no limit on attempts. The protocol
has no session. Therefore the device cannot connect a privilege level to a known host.

At level `sup` a program can do these operations:

| Operation | Command | Result |
|---|---|---|
| Read all 128 KB of the EEPROM | `getEE` | The DSP images, presets, calibration and serial numbers |
| Write any byte of the EEPROM | `setEE` | Permanent damage. The privilege level at `0x8D` can change. |
| Change the board serial number | `setSN` | A false device identity |
| Change the Shure SAP number | `setSAPSN` | False warranty and asset data |
| Read and write the DSP memory | `getDSP`, `setDSP` | Full control of the audio path |
| Start the bootloader | `bootLoad` | See F2 |
| Disable the touch panel | `disableUI` | The user cannot mute the microphone |
| Mute the analog headphone output | `hwMute` | |
| Remove power from the DSP | `dspPower off` | The audio processing stops with no message |

I tested `getEE`, `getDSP`, `getLed`, `myLed`, `fileLoc` and the LED and panel controls. I also
tested `bootLoad` and used it to write new firmware. See F2 and section 6.3 of `firmware.md`. I
did not test `setEE`, `setSN`, `setSAPSN` or `setDSP`, because those commands change data that the
device keeps.

The most important result is that the mute function is not reliable. The `micMute` command is at
level 0. The `disableUI` command needs only one `su` command, which is free. Therefore a local
program can remove the mute and stop the user from muting the microphone at the touch pad. The
same program can control the mute LED with `turnLed`, `brightness` and `myLed`. Therefore the LED
can show a condition that is not correct.

**Correction:** the privilege levels must use data that the host cannot supply. A challenge and
response with a key that is different for each device is the minimum. Physical confirmation for
level `sup` is better. A key that is the same in each copy of the desktop application does not
correct this fault. I recovered the firmware server password from the same application in a few
minutes.

---

## F2. The firmware images have no signature and the bootloader needs no key

**Level: high. I did this test on the device. I wrote new firmware with my own tool.**

No part of the update chain gives authenticity:

* `Updates.xml` gives no hash and no signature for any package. It gives only a path and a version.
* `MV7.1.2.19.pack` is a ZIP file. `MV7.manifest.xml` gives versions and a DCID, but no signature.
* Each row of a `.cyacd` file ends with one checksum byte. It is the two's complement of the sum
  of the other bytes. The header field `checksumType=0x01` selects CRC-16 for the bootloader
  protocol. Both of these find transmission errors. Neither shows authenticity.
* The DSP data uses a 16-bit summation checksum in the `END` line.

The TLS connection to `wwb.shure.com` is the only protection, and it protects only the download.
An attacker with code on the host does not need the network. The `bootLoad` command is available
after a free `su` command. It puts the device in the Cypress bootloader.

**The bootloader needs no key. I did this test on the device.** The `bootLoad` command puts the
MV7 in the Cypress bootloader. It then connects to the USB bus with the same ID `14ed:1012` and
the product text `PSoC4 Bootloader`. I sent the `ENTER` command (`0x38`) with an empty data field
and the bootloader replied with status `0x00`, which is success:

```
ENTER          : siliconId=0x100F11A0  rev=0  bootloaderVersion=1.1.60
APP_STATUS(0)  : active=1        <- the running application
APP_STATUS(1)  : active=0
FLASH_SIZE(0)  : rows 43..1023   <- rows 0 to 42 hold the bootloader and are protected
```

A bootloader with a security key rejects an `ENTER` command that has no key. This one does not.
Therefore any program that can open the HID device can put the MV7 in the bootloader and write
any image to the application area. No signature and no key stops it. I wrote no flash.

The host also selects the version. Older packages stay on the server at
`https://wwb.shure.com/wireless/MV7.<version>/`, and no code on the device rejects an older image.

---

## F3. A stack overflow in `getBlock` stops the MCU

**Level: medium. Denial of service and a controlled pointer read. I did this test on the device.**

The handler `cmd_getBlock` at `0x0000bfbc` builds its reply in a stack buffer of **128 bytes**.
That buffer is immediately below the saved registers. The code adds text with no limit:

```c
undefined1 auStack_90 [128];                      /* at sp+0x48 */
...
sprintf(auStack_90, "block %s ", argv[1]);        /* argv[1] up to 118 characters */
if (!ok) strcat(auStack_90, "Not valid");
else     append_hex(auStack_90, blockdata, len);
strcat(auStack_90, "\n");
```

The prologue and the epilogue give the stack frame:

```
0x0000bfbc  push {r4, r5, r6, lr}     ; lr at sp+0xD4, r6 at sp+0xD0, r5 at sp+0xCC, r4 at sp+0xC8
0x0000bfbe  sub  sp, 0xc8             ; 200 bytes of local data, reply buffer at sp+0x48
...
0x0000c038  ldrb r0, [r4]             ; this reads from the address in the damaged r4
0x0000c03a  add  sp, 0xc8
0x0000c03c  pop  {r4, r5, r6, pc}
```

The buffer is from `sp+0x48` to `sp+0xC7`. Byte 128 of the buffer is the saved `r4`. The number of
bytes that the code writes is `6 + length(argv[1]) + 1 + 9 + 1 + 1`. The command line limit gives
`argv[1]` a maximum of 118 characters, because `getBlock ` uses 9 of the 127 characters.
Therefore the code can write **136 bytes** into a buffer of 128 bytes. It writes over all of the
saved `r4` and all of the saved `r5`.

I sent longer and longer arguments. After each one I read `fwVersion` to find the condition of the
device:

```
arglen=110  reply='block BBBB...'   condition='fwVersion=0.0.49.0'
arglen=111  OK
arglen=112  OK
arglen=113  OK
arglen=114  CRASH   -> no reply. The device connected to the USB bus again (devnum 044 to 047).
```

The threshold agrees with the arithmetic. With 113 characters the last byte is the NUL at buffer
index 130. The highest byte of `r4` keeps its value `0x20`, which is a correct SRAM address. With
114 characters the NUL goes to index 131 and `r4` becomes `0x000A6469`. That address is after the
end of the 256 KB flash. Therefore `ldrb r0,[r4]` causes a bus fault. On ARMv6-M a bus fault
becomes a HardFault. The device resets and connects to the USB bus again.

After the reset all of the settings were correct: `volume=-5dB`, `inputGain=36dB`, `micMute=off`,
`dimMode=on`, `dspMode=5`, `pkgVersion=1.2.17`, and the two serial numbers. That was the package
version at the time of this test. The settings are in
the EEPROM. Therefore this fault causes a reset but no damage.

**The `getBlock` command is at level 2 (`adm`), which is the level after power-on. The `su`
command is not necessary.**

The fault gives a denial of service and a read from an address that the attacker selects. The
device does not send the result of that read to the attacker. The overflow reaches the saved `r4`
and `r5`. It stops 4 bytes before the saved `r6` and 8 bytes before the saved `lr`. Therefore
there is no control of `pc`.

That margin of 8 bytes is an accident of the stack frame and the 127-character line limit. One
more saved register, a longer error text, or a larger block gives control of `pc` through
`pop {r4,r5,r6,pc}`. Correct this fault as a possible code execution fault, not only as a crash.

---

## F4. An unbounded `sprintf` into a 32-byte buffer

**Level: low. From the code. The device test showed no failure.**

The dispatcher at `FUN_0000a4cc` contains this code:

```c
undefined1 auStack_164 [32];
...
sprintf(auStack_164, "No such command: %s\n", argv[0]);   /* argv[0] up to 127 characters */
```

The prefix is 17 bytes. The attacker supplies up to 127 bytes. With the terminator this gives 145
bytes in a buffer of 32 bytes, which is an overflow of 113 bytes. The overflow damages the token
pointer array and the copy of the input line. The code does not use either of them after this
point. The stack frame is `0x164` bytes deep, so the overflow cannot reach the saved registers.

I sent arguments of 20 to 127 characters. The device replied correctly each time and continued to
operate. This is a true fault with no result at this time. The same argument about future changes
applies as in F3.

---

## F5. Unbounded scans in the serial number handlers

**Level: low. From the code.**

The handler `cmd_getSN` reads 27 bytes from the EEPROM into a stack buffer of 32 bytes. It then
looks for the first byte that is not printable. The index is 8 bits and has **no upper limit**:

```c
byte abStack_28 [32];
ee_read(0xa4, 0x1b, abStack_28);
uVar3 = 0;
while (isprint(abStack_28[uVar3])) uVar3 = uVar3 + 1 & 0xff;   /* no limit */
if (0x1a < uVar3) uVar3 = 0x1a;                                /* the limit comes too late */
```

If EEPROM addresses `0xA4` to `0xBE` hold 27 printable bytes with no terminator, the scan reads up
to 255 bytes after the buffer. The write that follows has a correct limit. Therefore this is a
read fault only. It also needs an attacker who can use `setEE` or `setSN` first. The handler
`cmd_setSN` uses the same pattern on its argument.

---

## F6. The device can report a false version

**Level: low.**

The `pkgVersion` command is at table level 0. During an update session it writes the three version
strings to the EEPROM. Therefore a host that can complete an `update` sequence can make the device
report any version. The update software compares the version from the device with `Updates.xml`.
Therefore a device can appear correct while it runs old firmware, or it can receive an update that
it does not need.

The handler compares the DSP version argument with the version that it calculates from the DSP
file. Therefore this one field cannot be false.

---

## F7. Fixed credentials in the application

**Level: information only.**

MOTIV Mix contains its source maps. Therefore all of its configuration is in plain text. The
configuration files `app-config.production.ts`, `app-config.development.ts` and
`app-config.internal.ts` hold these items:

| Item | Count | Function |
|---|---|---|
| Firmware server user name and password | 2 sets | HTTP basic authentication for the package server |
| Release API keys | 2 | The `x-api-key` header for the application update service |
| Analytics project tokens | 3 | Mixpanel |
| Error report tokens | 4 | Backtrace |
| OAuth2 client IDs | 3 | Okta |

**This document gives no credential.** Section 1 of `README.md` gives the four steps that recover
the source. The values are then in plain text in those three files.

The credentials are the same in each copy of the application, and the firmware server holds public
firmware. Therefore the effect is small. Two points are still important. The credentials of the
test server and the internal server must not be in a build that goes to customers. Also, a build
that includes full source maps makes every secret, internal address and code path public.

---

## Correct behavior that I tested

The upload protocol for the EEPROM files is well designed. Section 6.1 of `firmware.md` gives the
full description. These are the important properties:

* The device writes to the file slot that is not in use.
* The device reads back each 64-byte page and compares it before it continues.
* The device calculates the checksum from the data that it read back.
* The declared file length limits the number of bytes that the device writes.
* The `END` checksum must agree, or the file does not become active.
* The device makes all 11 files active in one operation, after the last file.
* The `pkgVersion` command writes a new version only after that operation.
* A stop of 2000 ms ends the raw mode and writes nothing.

Other correct code:

* The tokenizer at `0x0000a370` has correct limits. It stops at 10 tokens, and the NULL terminator
  goes into the last position of an array of 11 entries.
* The handler `cmd_setBlock` uses the same reply pattern as `cmd_getBlock`, but its buffer is 152
  bytes and its maximum input is shorter. It does not overflow.
* The `cmd_getEE` handler limits the address to `0x1FFFF` and the count to 256. Its buffer is 260
  bytes.
* The device enforces the 128-character line limit. It replies `Data Full` and continues to
  operate. I tested lines of up to 255 characters.
