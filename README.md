# Shure MV7: reverse engineering report


This report is about the Shure MV7 USB microphone. Its USB ID is `14ed:1012`.

The host software is Shure MOTIV Mix 1.8.0.548 for Windows. I also examined version 1.7.1.1.
I did all device tests on an MV7 with a USB connection to the computer. That microphone had
package version 1.2.17 at the start. Section 6.3 of `firmware.md` gives the update that I did with
my own tool at the end of this work. The microphone now has package 1.2.19.

**Live tool: [the MV7 WebHID console](https://schlarpc.github.io/shure-mv7-firmware-re/).**
A web page speaks the vendor HID protocol directly. It gives controls for the audio settings, the
DSP, the panel and the LEDs, plus a terminal to the command console. It needs a Chromium browser
and a connected microphone. It sends no firmware. The page is [`web/index.html`](web/index.html)
and it has no dependencies.

| Document | Contents |
|---|---|
| [`hid-protocol.md`](hid-protocol.md) | The vendor HID protocol: transport, frames, all commands, asynchronous events |
| [`firmware.md`](firmware.md) | Firmware container, silicon, memory map, architecture, DSP, update method |
| [`security.md`](security.md) | Security faults, with one crash that I caused on the device |
| [`web/index.html`](web/index.html) | WebHID console: controls and a terminal, in one HTML file |
| [`tools/mv7ctl.py`](tools/mv7ctl.py) | Client for the console protocol |
| [`tools/mv7update.py`](tools/mv7update.py) | Firmware updater: pre-flight, DSP upload, MCU flash |
| [`tools/sigmadsp_disasm.py`](tools/sigmadsp_disasm.py) | SigmaDSP program decoder |
| [`tools/blockmap.py`](tools/blockmap.py) | DSP block descriptor decoder |
| [`tools/dsp_decode.py`](tools/dsp_decode.py) | DSP image decoder with register names |
| [`tools/fetch_packages.sh`](tools/fetch_packages.sh) | Downloads the firmware packages |
| [`data/`](data/) | Manifests, the recovered command table, DSP analysis results |
| [`data/adau1761_analysis.md`](data/adau1761_analysis.md) | DSP program findings |

---

## 1. How to unpack the installer

The two installers are VMware BitRock InstallBuilder packages. Each one is a TclKit `setup.exe`
with a chain of LZMA streams in the PE overlay. The string
`::bitrock_tcl_is_using_only_s32_dll_path` in the `.text` section identifies this installer type.

You can decompress the LZMA chain directly. There are 31 streams. One `0xFF` byte divides them,
and the first stream starts at offset 1. But this method gives no file names. The payload is one
block of file data with no header, and the directory is in the encrypted project data.

It is faster to run the installer with Wine. This method gives a correct file tree:

```sh
export WINEPREFIX=$PWD/tmp/wineprefix
wineboot -u && winecfg -v win11
# The 1.8.0 installer needs a build number higher than the 22000 that Wine reports:
wine reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion' /v CurrentBuild      /d 26100 /f
wine reg add 'HKLM\Software\Microsoft\Windows NT\CurrentVersion' /v CurrentBuildNumber /d 26100 /f
wine MOTIV-Mix-Windows-1.8.0.exe --mode unattended --unattendedmodeui none --eula_choice eula_accepted
```

If you do not increase the build number, the 1.8.0 installer stops with this message:
`MOTIV Mix only Supports Windows 11.` Version 1.7.1.1 installs on the default Wine `win11`
mode without this change.

### The contents of the installer

MOTIV Mix is an Electron application. It includes its webpack source maps. The file
`main.js.map` is 44 MB. Therefore you can recover the original TypeScript source. This gives
4,499 files, which include all of the `@shure/system-api-*` code.

To recover the source, read the asar archive with a 16-byte header. Then read the
`sourcesContent` array.

| Component | Function |
|---|---|
| `MOTIV Mix.exe` and `resources/app.asar` | The Electron front end: an Angular renderer and a Node main process |
| `resources/phoenix-server/bin/windows/x86_64/ShureDeviceManager.exe` | The C++ and gRPC device manager, 110 MB. Wireless Workbench and Designer also use it. |
| `resources/phoenix-server/{DDL,FD,DCIDMap}.dat` | Device definition databases in AES-encrypted ZIP files |
| `resources/build/Release/*.node` and `AELibrary.dll` | The native audio engine. It includes `.pdb` and `.ipdb` symbol files. |
| `winVADx/MotivMixVAD.sys` | The virtual audio device driver |

The Electron code does not communicate with the microphone. It configures `ShureDeviceManager`
through gRPC. `ShureDeviceManager` controls the USB HID connection.

---

## 2. How the software downloads firmware

The file `src/app-config/app-config.production.ts` came from the source map. It contains this
configuration:

```ts
systemApi: { application: { firmwareUpdate: { packageServer: {
    syncIntervalMinutes: 1440,
    url: 'https://wwb.shure.com/wireless/',
    username: '<redacted>',
    password: '<redacted>'
} } } }
```

This document gives no credential. To read the user name and the password, do these steps:

1. Install MOTIV Mix. Section 1 of this document gives the steps for Wine.
2. Extract `resources/app.asar`.
3. Recover the source from `main.js.map` with the `sourcesContent` array.
4. Open `src/app-config/app-config.production.ts` and find the `packageServer` block.

The two values are in plain text. The file `app-config.development.ts` holds the values for the
test server.

This is the Wireless Workbench package server. All Shure desktop applications use it.
`ShureDeviceManager` downloads an index file with the name `Updates.xml` from the root of the
server. It uses HTTPS with HTTP basic authentication. The two related functions are
`PHX::PeriodicServerManifestDownloader::DownloadManifest` and
`PHX::XMLServerManifestParser::Parse`.

To download the index, run this command with the credentials from the application:

```sh
curl -u "$SHURE_PKG_USER:$SHURE_PKG_PASS" https://wwb.shure.com/wireless/Updates.xml
```

The file has 464 `<PackageUpdate>` entries for all Shure products. This is the MV7 entry:

```xml
<PackageUpdate>
    <Name>MV7</Name>
    <Description>Motiv MV7</Description>
    <Version>1.2.19</Version>
    <ReleaseDate>2024-09-26</ReleaseDate>
    <UpdateFile>MV7.1.2.19/MV7.1.2.19.pack</UpdateFile>
    <FullVersion>1.2.19.0</FullVersion>
</PackageUpdate>
```

The development builds and the internal builds use a different server, with different
credentials. Both are in `app-config.development.ts` and `app-config.internal.ts`.

### Version data

| Item | Version |
|---|---|
| Last MV7 package on the server | **1.2.19**, released 2024-09-26 |
| Package on the connected microphone, at the start | **1.2.17** |
| Package on the same microphone, after this work | **1.2.19** |
| MCU firmware on the microphone | `0.0.49.0`. Package 1.2.19 contains `0.0.52.0`. |
| DSP on the microphone | `0.0.5.22`. Package 1.2.19 contains the same version. |

The microphone is two package versions old. Only the PSoC application image is different. The
DSP data is the same in both packages.

### The release history

`Updates.xml` gives only the newest package, but the older ones stay on the server at
`https://wwb.shure.com/wireless/MV7.<version>/`. I downloaded 1.2.15 to 1.2.19 and compared them.
Version 1.2.14 and version 1.2.20 give HTTP 404. Therefore 1.2.19 is the last MV7 release.

| Package | MCU (type A) | DSP (type D) | What changed from the version before |
|---|---|---|---|
| 1.2.15 | 0.0.47.0 | 0.0.5.21 | — |
| 1.2.16 | 0.0.47.0 | 0.0.5.22 | The DSP data and the DSP settings only |
| 1.2.17 | 0.0.49.0 | 0.0.5.22 | The two `.cyacd` images only |
| 1.2.18 | 0.0.52.0 | 0.0.5.22 | The two `.cyacd` images only |
| 1.2.19 | 0.0.52.0 | 0.0.5.22 | **No firmware change.** The manifest DCID only. |

Two results are important:

* **1.2.19 and 1.2.18 hold the same firmware.** The `.cyacd` files of the two packages are the
  same in each byte. The change in 1.2.19 is the DCID in the manifest. Package 1.2.17 and package
  1.2.18 give `14ED1012-0000-0000-0000-000000000000`, which is a value made from the USB numbers.
  Package 1.2.19 gives `04F37A8C-FD5A-11E3-ACBA-0015C5F3F612`, which is the value that the device
  reports. Therefore 1.2.19 corrects the match between the package and the device. Use 1.2.19.
* **The DSP data has not changed since 1.2.16.** Each change after that is in the MCU image.
  Therefore a correction to the audio in 1.2.18 is in the MCU firmware, which controls the USB
  audio transport. It is not in the DSP, which does the voice processing only.

A comparison of the 1.2.17 and the 1.2.19 MCU images gives more data:

* The two images have a build date in the code. It is `Sep 12 2022 16:43:45` for 1.2.17 and
  `Nov  9 2023 14:57:29` for 1.2.19.
* No text string is different, and the command table is the same. The 48 commands and their
  privilege levels do not change. Therefore the security results in `security.md` apply to both
  versions.
* 136 of the 410 flash rows are different. There are no new functions, only changes to the code.

The file `MV7.1.2.19.pack` is 181,397 bytes. Its SHA-256 value is
`94a7a8ef6f6b0f6ba27a0d138cc708312fd31edb241a590e709ee30b4a0c00c4`. It is a ZIP file that
contains these items:

```
MV7.manifest.xml                    Package data and the DCID
MV7.embeddedManifest.hex            File and version table for EEPROM offset 0
MonoIn_StereoOut_24b_96KHz_1.cyacd  PSoC application image, slot 1
MonoIn_StereoOut_24b_96KHz_2.cyacd  PSoC application image, slot 2
DSP-MV7.dat                         DSP data and presets. See firmware.md.
DSP-Settings-MV7.xml                DSP parameter map for the user interface
UpdateInstructions.xml              The update operations, in sequence
```

---

## 3. Data from the connected microphone

```
$ fwVersion       fwVersion=0.0.49.0
$ pkgVersion      pkgVersion=1.2.17
$ dspVersion      dspVersion=0.0.5.22
$ deviceType      deviceType=04F37A8C-FD5A-11E3-ACBA-0015C5F3F612
$ interfaceId     interfaceId=0.3.0
$ getSampleRate   CurrentRate=48000  outDepth=24  inDepth=24
$ dspMode         dspMode=5
```

The `deviceType` value is the same as the `<DCID>` value in `MV7.manifest.xml`. The host software
uses this GUID to select the correct firmware package for a connected device.

The `interfaceId` value selects the protocol translator in the host software. This microphone
uses `MV7CliMsgTranslator_0_3_0`. `ShureDeviceManager` contains four translators: 0.0.1, 0.1.0,
0.2.0 and 0.3.0.

---

## 4. Summary of the results

The manufacturer interface of the MV7 is an ASCII command console on a vendor HID interface. The
console has 48 commands. It has no authentication, no message frames, no checksums and no session.

The console has four privilege levels: `nor`, `dev`, `adm` and `sup`. The command `su sup` gives
the highest level. It needs no password, no token and no challenge. At this level you can do
these operations:

- read the EEPROM and write to the EEPROM
- read the DSP registers and write to the DSP registers
- change the two serial numbers
- start the LED test mode and the button test mode
- start the bootloader

The firmware packages have no signature. The only protection is the TLS connection to
`wwb.shure.com` and a 16-bit summation checksum.

The `getBlock` command has a stack overflow fault. It writes over the saved registers and causes
a HardFault in the MCU. The default privilege level is sufficient, so the `su` command is not
necessary. I did this test on the microphone. The microphone reset itself and connected to the
USB bus again.

The file [`security.md`](security.md) gives more data about these faults.

---

## 5. How to repeat this work

To read data from the microphone, run these commands. You need read and write permission on the
hidraw device.

```sh
sudo python3 tools/mv7ctl.py fwVersion pkgVersion deviceType
sudo python3 tools/mv7ctl.py 'su sup' help

# Read 256 bytes of the external EEPROM at address 0x0C00:
sudo python3 tools/mv7ctl.py 'su sup' 'getEE 0xC00 256'
```

On Linux the MV7 makes two hidraw devices. The console is the device with usage page `0xFF00` in
its report descriptor. That descriptor starts with the bytes `06 00 ff 09 01`. It is USB
interface 3.

The other hidraw device is the consumer control collection for the mute key. The `mv7ctl.py`
tool selects the correct device automatically.

If you do not want to use `sudo`, install this udev rule:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="14ed", ATTRS{idProduct}=="1012", MODE="0660", GROUP="users"
```

---

## License

The tools and the documents in this repository are under the MIT license. See
[`LICENSE`](LICENSE).

That license covers this work only. The small manifest files under `data/` are extracts from a
Shure firmware package and stay the property of Shure Incorporated. This repository holds no
firmware image, no credential and no serial number. The script
[`tools/fetch_packages.sh`](tools/fetch_packages.sh) downloads the packages from Shure.

Shure, MOTIV and MV7 are trademarks of Shure Incorporated. This work has no connection with Shure.
