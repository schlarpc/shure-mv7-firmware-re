#!/usr/bin/env python3
"""Client for the Shure MOTIV vendor-HID text console (MV7, 14ed:1012).

The MV7 exposes an ASCII command console on its vendor HID interface (usage page
0xFF00, interface 3).  There is no framing: command bytes go straight into the
64-byte OUT report and replies come back as ASCII in 64-byte IN reports.  The
device assembles a command line from consecutive OUT reports until it sees a NUL,
so lines longer than 63 characters must be split -- this client does that.

Usage:
    mv7ctl.py fwVersion pkgVersion deviceType
    mv7ctl.py 'su sup' help
    mv7ctl.py --repl
    mv7ctl.py --dump-eeprom out.bin

Needs read/write on the hidraw node; either run as root or install a udev rule:
    SUBSYSTEM=="hidraw", ATTRS{idVendor}=="14ed", ATTRS{idProduct}=="1012", MODE="0660", GROUP="users"
"""
import argparse
import glob
import os
import re
import select
import sys
import time

VID, PID = 0x14ED, 0x1012
REPORT_LEN = 64
MAX_LINE = 127          # device rejects >=128 with "Data Full"


def find_dev(vid=VID, pid=PID):
    """Return the hidraw node for the vendor-page collection (not the consumer one)."""
    want = "HID_ID=0003:%08X:%08X" % (vid, pid)
    best = None
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            uevent = open(os.path.join(path, "device/uevent")).read()
        except OSError:
            continue
        if want not in uevent.upper():
            continue
        node = "/dev/" + os.path.basename(path)
        try:
            rd = open(os.path.join(path, "device/report_descriptor"), "rb").read()
        except OSError:
            rd = b""
        if rd.startswith(b"\x06\x00\xff"):   # usage page 0xFF00 -> the console
            return node
        best = best or node
    return best


class MV7:
    def __init__(self, dev=None):
        self.dev = dev or find_dev()
        if not self.dev:
            raise SystemExit("no Shure MV7 vendor HID interface found")
        self.fd = os.open(self.dev, os.O_RDWR | os.O_NONBLOCK)

    def close(self):
        os.close(self.fd)

    def _drain(self, timeout, idle=0.12):
        """Read reports until the endpoint stays quiet for `idle` seconds."""
        out, deadline = [], time.time() + timeout
        while time.time() < deadline:
            if select.select([self.fd], [], [], 0.02)[0]:
                try:
                    out.append(os.read(self.fd, REPORT_LEN))
                except BlockingIOError:
                    continue
                deadline = max(deadline, time.time() + idle)
        return out

    def cmd(self, line, wait=0.6):
        """Send one command line, return (text, raw_reports)."""
        payload = line.encode()
        if len(payload) > MAX_LINE:
            raise ValueError("command line limited to %d characters" % MAX_LINE)
        self._drain(0.05)                       # discard stale async events
        payload += b"\x00"                      # NUL terminates the line
        for i in range(0, len(payload), REPORT_LEN):
            chunk = payload[i:i + REPORT_LEN]
            os.write(self.fd, chunk + b"\x00" * (REPORT_LEN - len(chunk)))
        pkts = self._drain(wait)
        text = "".join(p.split(b"\x00")[0].decode("latin1") for p in pkts)
        return text, pkts

    def listen(self, seconds):
        """Yield unsolicited events (button taps, slider, rate changes, ...)."""
        end = time.time() + seconds
        while time.time() < end:
            if select.select([self.fd], [], [], 0.5)[0]:
                s = os.read(self.fd, REPORT_LEN).split(b"\x00")[0].decode("latin1")
                if s.strip():
                    yield s.strip()

    def read_eeprom(self, addr, count=256, tries=4):
        """Read `count` bytes (max 256) from the external EEPROM at `addr`.

        The device aligns each dump row down to a 16-byte boundary and prints
        `--` for the cells before the requested address, so an unaligned start
        must be handled by address, not by position.
        """
        for _ in range(tries):
            text, _ = self.cmd("getEE 0x%X %d" % (addr, count), 1.0)
            got = {}
            for m in re.finditer(r"^([0-9A-F]{5}): ((?:(?:[0-9A-F]{2}|--) ?)+)", text, re.M):
                base = int(m.group(1), 16)
                for i, tok in enumerate(m.group(2).split()):
                    if tok != "--":
                        got[base + i] = int(tok, 16)
            if all((addr + i) in got for i in range(count)):
                return bytes(got[addr + i] for i in range(count))
            time.sleep(0.05)
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmds", nargs="*", help="commands to run, in order")
    ap.add_argument("--dev", help="hidraw node (default: autodetect)")
    ap.add_argument("--wait", type=float, default=0.6, help="reply timeout in seconds")
    ap.add_argument("--raw", action="store_true", help="also print raw reports")
    ap.add_argument("--repl", action="store_true", help="interactive prompt")
    ap.add_argument("--listen", type=float, metavar="SECS",
                    help="print unsolicited events for SECS seconds")
    ap.add_argument("--dump-eeprom", metavar="FILE",
                    help="dump the full 128 KB external EEPROM (needs 'su sup', ~9 min)")
    args = ap.parse_args()

    m = MV7(args.dev)
    print("# device: %s" % m.dev, file=sys.stderr)
    try:
        for c in args.cmds:
            text, pkts = m.cmd(c, args.wait)
            print("$ %s" % c)
            print(text.rstrip("\n") if text else "(no reply)")
            if args.raw:
                for p in pkts:
                    print("   ", p.hex())
            print()

        if args.dump_eeprom:
            m.cmd("su sup", 0.4)
            size, out, t0 = 0x20000, bytearray(), time.time()
            for a in range(0, size, 256):
                chunk = m.read_eeprom(a)
                if chunk is None:
                    print("read failed at 0x%05X" % a, file=sys.stderr)
                    chunk = b"\x00" * 256
                out += chunk
                if a % 0x2000 == 0:
                    print("0x%05X  %.0fs" % (a, time.time() - t0), file=sys.stderr, flush=True)
            open(args.dump_eeprom, "wb").write(bytes(out))
            print("wrote %d bytes to %s" % (len(out), args.dump_eeprom), file=sys.stderr)

        if args.listen:
            print("# listening %.0fs -- tap mute, slide the volume strip..." % args.listen,
                  file=sys.stderr)
            for evt in m.listen(args.listen):
                print("EVT %r" % evt, flush=True)

        if args.repl:
            print("# type commands, 'quit' to exit", file=sys.stderr)
            while True:
                try:
                    line = input("mv7> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if line in ("quit", "exit"):
                    break
                if not line:
                    continue
                text, _ = m.cmd(line, args.wait)
                print(text.rstrip("\n") if text else "(no reply)")
    finally:
        m.close()


if __name__ == "__main__":
    main()
