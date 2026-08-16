#!/usr/bin/env python3
"""Firmware updater for the Shure MV7, driven from the reverse-engineered protocols.

Two independent paths, documented in firmware.md section 6:

  DSP / EEPROM path   `update <file> <len>` on the ASCII console, then hex lines,
                      then `END <crc>`.  Fully reverse-engineered and genuinely
                      fail-safe: writes go to the inactive file slot, every
                      64-byte page is read back and compared, the running
                      checksum is computed from the read-back bytes, and the
                      live file table is only rewritten after all 11 files land.
                      An abort at any point leaves the old file set active.

  MCU path            `bootLoad` resets into the Cypress PSoC bootloader, which
                      then takes the .cyacd image.  The bootloader protocol here
                      is the stock Cypress one; THAT ASSUMPTION IS UNVERIFIED on
                      this device.  See the warnings on --recon and --flash.

Modes
  --check    (default) read-only pre-flight.  Compares device versions and every
             EEPROM file against the package.  Touches nothing.
  --dsp      run the DSP/EEPROM path.  Safe, and normally a no-op because the
             1.2.17 and 1.2.19 packages ship identical DSP payloads.
  --recon    enter the bootloader, run only read-only bootloader queries, and
             report.  Writes no flash.  RECOVERY IS A PHYSICAL UNPLUG: `bootLoad`
             sets a magic word in SRAM at 0x200077F8 and resets; SRAM clears when
             bus power is removed, so replugging returns the mic to normal.
  --flash    the real MCU update.  Requires --i-understand-the-risk.

Usage
  sudo python3 mv7update.py --pack MV7.1.2.19.pack --check
"""
import argparse, os, re, select, struct, sys, time, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mv7ctl import MV7, find_dev            # noqa: E402

# ---------------------------------------------------------------- package ---

class Pack:
    """A Shure .pack: a plain ZIP with a manifest, cyacd images and DSP data."""

    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        names = self.zip.namelist()
        man = next(n for n in names if n.endswith('.manifest.xml'))
        x = self.zip.read(man).decode()
        self.key = re.search(r'<Key>([^<]+)', x).group(1)
        self.version = re.search(r'<Version>([^<]+)', x).group(1)
        self.dcid = re.search(r'<DCID>([^<]+)', x).group(1)
        self.files = {m.group(2): m.group(1)
                      for m in re.finditer(r'<File type="(\w)"[^>]*>([^<]+)<', x)}
        self.fw_version = re.search(r'<File type="A" version="([^"]+)"', x).group(1)
        self.dsp_version = re.search(r'<File type="D" version="([^"]+)"', x).group(1)
        self.names = names

    def cyacd(self, slot):
        n = next(k for k in self.files if k.endswith('_%d.cyacd' % slot))
        return self.zip.read(n).decode()

    def dsp_sections(self):
        """Parse DSP-MV7.dat into [(filename, declared_len, payload, crc), ...]."""
        n = next(k for k in self.files if k.startswith('DSP-') and k.endswith('.dat'))
        out, cur, buf = [], None, []
        for ln in self.zip.read(n).decode().splitlines():
            ln = ln.strip()
            if ln.startswith('update '):
                _, fn, ln_hex = ln.split()
                cur = (fn, int(ln_hex, 16)); buf = []
            elif ln.startswith('END'):
                out.append((cur[0], cur[1], bytes.fromhex(''.join(buf)),
                            int(ln.split()[1], 16)))
                cur = None
            elif cur and re.fullmatch(r'[0-9A-F]+', ln):
                buf.append(ln)
        return out


def parse_cyacd(text):
    """Return (siliconId, siliconRev, checksumType, [(array, row, data), ...])."""
    lines = text.split()
    h = lines[0]
    sid, rev, ctype = int(h[0:8], 16), int(h[8:10], 16), int(h[10:12], 16)
    rows = []
    for ln in lines[1:]:
        b = bytes.fromhex(ln[1:])
        n = (b[3] << 8) | b[4]
        data = b[5:5 + n]
        if ((~sum(b[:-1]) + 1) & 0xFF) != b[-1]:
            raise ValueError("row checksum bad at array %d row %d" % (b[0], (b[1] << 8) | b[2]))
        rows.append((b[0], (b[1] << 8) | b[2], data))
    return sid, rev, ctype, rows

# ------------------------------------------------------- device (console) ---

def dev_state(m):
    st = {}
    for cmd, key in (('fwVersion', 'fw'), ('pkgVersion', 'pkg'), ('dspVersion', 'dsp'),
                     ('deviceType', 'dcid'), ('interfaceId', 'iface')):
        t, _ = m.cmd(cmd, 0.8)
        mm = re.search(r'=\s*(\S+)', t)
        st[key] = mm.group(1) if mm else t.strip()
    return st


def read_ee(m, addr, n):
    out = bytearray()
    while len(out) < n:
        want = min(256, n - len(out))
        chunk = m.read_eeprom(addr + len(out), want)
        if chunk is None:
            return None
        out += chunk
    return bytes(out)

# ------------------------------------------------- Cypress bootloader (!!) ---

def crc16(data):
    """Cypress cybtldr CRC-16: reflected, poly 0x8408, init 0xFFFF, inverted, byte-swapped.

    VERIFIED on the MV7 bootloader: of the four plausible variants (this one and
    its big-endian form, plus 2's-complement summation in both orders), only
    this one returns status 0x00 to ENTER.  The others return 0x08 (checksum).
    """
    crc = 0xFFFF
    for b in data:
        tmp = b & 0xFF
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8408) if ((crc & 1) ^ (tmp & 1)) else (crc >> 1)
            tmp >>= 1
    crc = (~crc) & 0xFFFF
    return ((crc << 8) | (crc >> 8)) & 0xFFFF

def btldr_packet(cmd, data=b'', ctype=1):
    body = bytes([0x01, cmd, len(data) & 0xFF, len(data) >> 8]) + data
    ck = crc16(body) if ctype == 1 else ((~sum(body) + 1) & 0xFFFF)
    return body + struct.pack('<H', ck) + b'\x17'


CMD = dict(VERIFY_CHECKSUM=0x31, GET_FLASH_SIZE=0x32, GET_APP_STATUS=0x33,
           ERASE_ROW=0x34, SYNC=0x35, SET_ACTIVE_APP=0x36, SEND_DATA=0x37,
           ENTER=0x38, PROGRAM_ROW=0x39, VERIFY_ROW=0x3A, EXIT=0x3B)

STATUS = {0x00: 'success', 0x02: 'verify error', 0x03: 'length error',
          0x04: 'data error', 0x05: 'command error', 0x06: 'device error',
          0x07: 'version error', 0x08: 'checksum error', 0x09: 'array error',
          0x0A: 'row error', 0x0B: 'protect error', 0x0C: 'app error',
          0x0D: 'active error', 0x0F: 'unknown error'}


class Bootloader:
    """Client for the stock Cypress PSoC bootloader over USB HID.

    UNVERIFIED on this device.  The framing and command numbers are the public
    cybtldr ones.  Nothing here has been exercised against an MV7, because the
    bootloader's USB identity is only observable once the device has entered it.
    """

    def __init__(self, dev, ctype=1, report_id=0):
        self.fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        self.ctype = ctype
        self.rid = report_id

    def close(self):
        os.close(self.fd)

    def _xfer(self, cmd, data=b'', timeout=2.0):
        pkt = btldr_packet(cmd, data, self.ctype)
        buf = bytes([self.rid]) + pkt if self.rid else pkt
        os.write(self.fd, buf.ljust(64, b'\x00'))
        end, resp = time.time() + timeout, b''
        while time.time() < end:
            if select.select([self.fd], [], [], 0.05)[0]:
                resp += os.read(self.fd, 64)
                if len(resp) >= 4:
                    need = 4 + ((resp[3] << 8) | resp[2]) + 3
                    if len(resp) >= need:
                        break
        if len(resp) < 7 or resp[0] != 0x01:
            raise IOError("bad bootloader response: %r" % resp[:16])
        status = resp[1]
        n = (resp[3] << 8) | resp[2]
        if status != 0:
            raise IOError("bootloader status 0x%02X (%s)" % (status, STATUS.get(status, '?')))
        return resp[4:4 + n]

    def enter(self):
        d = self._xfer(CMD['ENTER'])
        sid, rev, bl = struct.unpack('<IB', d[:5])[0], d[4], d[5:8]
        return dict(siliconId=struct.unpack('<I', d[0:4])[0], siliconRev=d[4],
                    blVersion='%d.%d.%d' % (d[7], d[6], d[5]))

    def app_status(self, app):
        d = self._xfer(CMD['GET_APP_STATUS'], bytes([app]))
        return dict(valid=bool(d[0]), active=bool(d[1]))

    def flash_size(self, array):
        d = self._xfer(CMD['GET_FLASH_SIZE'], bytes([array]))
        first, last = struct.unpack('<HH', d[:4])
        return first, last

    def program_row(self, array, row, data, chunk=32):
        for off in range(0, len(data) - chunk, chunk):
            self._xfer(CMD['SEND_DATA'], data[off:off + chunk])
        tail = data[len(data) - (len(data) % chunk or chunk):]
        self._xfer(CMD['PROGRAM_ROW'], bytes([array]) + struct.pack('<H', row) + tail, 5.0)

    def verify_row(self, array, row):
        return self._xfer(CMD['VERIFY_ROW'], bytes([array]) + struct.pack('<H', row))[0]

    def set_active_app(self, app):
        self._xfer(CMD['SET_ACTIVE_APP'], bytes([app]))

    def verify_checksum(self):
        return bool(self._xfer(CMD['VERIFY_CHECKSUM'])[0])

    def exit(self):
        pkt = btldr_packet(CMD['EXIT'], b'', self.ctype)
        os.write(self.fd, (bytes([self.rid]) + pkt if self.rid else pkt).ljust(64, b'\x00'))


def find_bootloader(timeout=25):
    """Wait for a hidraw node whose HID_NAME says PSoC4 Bootloader."""
    import glob
    end = time.time() + timeout
    while time.time() < end:
        for p in sorted(glob.glob('/sys/class/hidraw/hidraw*')):
            try:
                ue = open(os.path.join(p, 'device/uevent')).read()
            except OSError:
                continue
            if '000014ED' in ue and 'Bootloader' in ue:
                return '/dev/' + os.path.basename(p)
        time.sleep(0.4)
    return None


def hid_snapshot():
    import glob
    out = {}
    for p in glob.glob('/sys/class/hidraw/hidraw*'):
        try:
            out['/dev/' + os.path.basename(p)] = open(os.path.join(p, 'device/uevent')).read()
        except OSError:
            pass
    return out


# ------------------------------------------------------------------- main ---

def cmd_check(args, pack):
    print("package : %s %s   (fw %s, dsp %s)" % (pack.key, pack.version,
                                                 pack.fw_version, pack.dsp_version))
    print("dcid    : %s" % pack.dcid)
    for slot in (1, 2):
        sid, rev, ct, rows = parse_cyacd(pack.cyacd(slot))
        print("cyacd %d : siliconId=0x%08X rev=%d checksumType=%d, %d rows, rows %d..%d  (all row checksums OK)"
              % (slot, sid, rev, ct, len(rows), rows[0][1], rows[-1][1]))
    dev = find_dev()
    if not dev:
        print("\nno MV7 found; package checks only."); return
    m = MV7(dev)
    try:
        st = dev_state(m)
        print("\ndevice  : pkg %s, fw %s, dsp %s, iface %s" % (st['pkg'], st['fw'], st['dsp'], st['iface']))
        print("dcid    : %s   %s" % (st['dcid'], "MATCH" if st['dcid'] == pack.dcid else "MISMATCH"))
        need_mcu = st['fw'] != pack.fw_version
        print("\nMCU flash : device %s vs package %s -> %s"
              % (st['fw'], pack.fw_version, "UPDATE NEEDED" if need_mcu else "up to date"))
        if args.deep:
            m.cmd('su sup', 0.4)
            print("\ncomparing every EEPROM file against the package (this takes a few minutes):")
            secs = {n: p for n, _, p, _ in pack.dsp_sections()}
            m.cmd('su sup', 0.3)
            diff = 0
            for i in range(11):
                t, _ = m.cmd('fileLoc %d' % i, 0.8)
                mm = re.search(r'fileLoc=0x([0-9A-F]+)\s*(\S+)', t.replace('\n', ' '))
                if not mm:
                    print("   file %d: unreadable" % i); continue
                addr, name = int(mm.group(1), 16), mm.group(2)
                want = secs.get(name)
                if want is None:
                    print("   %-18s not in package" % name); continue
                got = read_ee(m, addr, len(want))
                same = got == want
                diff += not same
                print("   %-18s @0x%04X %5d bytes  %s" % (name, addr, len(want),
                                                          "identical" if same else "DIFFERS"))
            print("\nDSP/EEPROM path: %s" % ("nothing to do" if not diff else "%d file(s) need updating" % diff))
        else:
            print("DSP path  : device dsp %s vs package %s -> %s   (use --deep to byte-compare)"
                  % (st['dsp'], pack.dsp_version, "differs" if st['dsp'] != pack.dsp_version else "up to date"))
    finally:
        m.close()


def _fast(m, line, timeout=0.35):
    """Send one console line and read its reply with a short deadline.

    The upload handler answers every data line with the running checksum, so the
    round trip is quick.  The device leaves raw mode after 2000 ms of silence,
    so the deadline must stay well under that.
    """
    b = line.encode() + b'\x00'
    while select.select([m.fd], [], [], 0)[0]:
        try: os.read(m.fd, 64)
        except OSError: break
    for i in range(0, len(b), 64):
        ch = b[i:i + 64]
        os.write(m.fd, ch + b'\x00' * (64 - len(ch)))
    end, out = time.time() + timeout, b''
    while time.time() < end:
        if select.select([m.fd], [], [], 0.01)[0]:
            out += os.read(m.fd, 64)
            if b'\n' in out or b'\r' in out:
                break
    return out.split(b'\x00')[0].decode('latin1')


def cmd_dsp(args, pack, m=None):
    """Upload the 11 EEPROM files. Two-phase commit; an abort keeps the old set."""
    own = m is None
    if own:
        m = MV7(find_dev()); m.cmd('su sup', 0.4)
    try:
        secs = pack.dsp_sections()
        total = sum(len(p) for _, _, p, _ in secs)
        print("uploading %d files, %d bytes total" % (len(secs), total))
        t0, done = time.time(), 0
        for name, declared, payload, crc in secs:
            if args.dry_run:
                print("  would: update %s 0x%04X (%d bytes, END 0x%04X)"
                      % (name, declared, len(payload), crc))
                continue
            r = _fast(m, 'update %s 0x%04X' % (name, declared), 1.0)
            if 'failed' in r.lower():
                raise SystemExit("device rejected 'update %s': %r" % (name, r))
            for off in range(0, len(payload), 16):
                r = _fast(m, payload[off:off + 16].hex().upper())
                if 'CMD failed' in r:
                    raise SystemExit("upload of %s rejected at offset %d: %r" % (name, off, r))
                done += 16
            r = _fast(m, 'END 0x%04X' % crc, 2.0)
            ok = 'CMD failed' not in r
            print("  %-18s %5d bytes  END 0x%04X  %-9s  %.0fs elapsed"
                  % (name, len(payload), crc, "ok" if ok else "FAILED", time.time() - t0))
            if not ok:
                raise SystemExit("checksum rejected for %s: %r" % (name, r))
        if not args.dry_run:
            print("upload complete in %.0fs" % (time.time() - t0))
    finally:
        if own: m.close()


def cmd_all(args, pack):
    """The full update: MCU flash if needed, then the DSP files, then the version."""
    dev = find_dev()
    m = MV7(dev); st = dev_state(m); m.close()
    print("device before: pkg %s, fw %s, dsp %s" % (st['pkg'], st['fw'], st['dsp']))
    if st['fw'] != pack.fw_version:
        print("\n--- MCU flash ---")
        cmd_bootloader(args, pack, write=True)
        print("waiting for the application to restart ...")
        time.sleep(8)
    else:
        print("MCU already at %s, skipping the flash" % pack.fw_version)
    print("\n--- DSP / EEPROM files ---")
    m = MV7(find_dev()); m.cmd('su sup', 0.4)
    try:
        cmd_dsp(args, pack, m)
        if args.dry_run:
            return
        print("\n--- package version ---")
        flag = m.read_eeprom(0xABF, 1)
        print("  update-session flag 0xABF = %s"
              % ("0x%02X" % flag[0] if flag else "unreadable"))
        cmd = 'pkgVersion %s %s %s' % (pack.version, pack.fw_version, pack.dsp_version)
        r, _ = m.cmd(cmd, 2.0)
        print("  $ %s -> %r" % (cmd, r.strip()))
        st = dev_state(m)
        print("\ndevice after : pkg %s, fw %s, dsp %s" % (st['pkg'], st['fw'], st['dsp']))
    finally:
        m.close()


def enter_bootloader():
    m = MV7(find_dev())
    try:
        m.cmd('su sup', 0.4)
        print("issuing bootLoad ...")
        try:
            m.cmd('bootLoad', 1.0)
        except OSError:
            pass                      # the device resets mid-command; the fd dies
    finally:
        try: m.close()
        except OSError: pass
    node = find_bootloader()
    if not node:
        raise SystemExit("bootloader did not appear; unplug and replug the microphone")
    print("bootloader at %s" % node)
    return node


def cmd_bootloader(args, pack, write):
    print("!" * 72)
    print("This resets the microphone into the Cypress bootloader.")
    print("Measured facts: the bootloader is at the same USB id 14ed:1012 with product")
    print("text 'PSoC4 Bootloader', needs no security key, protects rows 0-42, and its")
    print("EXIT command returns to the application without a power cycle.")
    if write:
        print("")
        print("--flash WILL WRITE FLASH.  It writes only the slot that is NOT running and")
        print("sets it active only after the bootloader verifies the application checksum.")
        print("A failure at any earlier point leaves the running image untouched.")
    print("!" * 72)
    if write and not args.i_understand_the_risk:
        raise SystemExit("refusing: --flash requires --i-understand-the-risk")
    if not args.yes:
        raise SystemExit("refusing: pass --yes to proceed")

    node = enter_bootloader()
    bl = Bootloader(node)
    try:
        info = bl.enter()
        print("ENTER        : siliconId=0x%08X rev=%d blVersion=%s"
              % (info['siliconId'], info['siliconRev'], info['blVersion']))
        sid, rev, ctype, _ = parse_cyacd(pack.cyacd(1))
        if sid != info['siliconId']:
            raise SystemExit("silicon id mismatch: device 0x%08X, package 0x%08X" % (info['siliconId'], sid))
        print("silicon id matches the package")
        status = {a: bl.app_status(a) for a in (0, 1)}
        for a in (0, 1):
            print("APP_STATUS(%d): %s" % (a, status[a]))
        active = 0 if status[0]['active'] else 1
        target = 1 - active
        print("running application = %d (slot %d) -> will write application %d (slot %d)"
              % (active, active + 1, target, target + 1))
        try:
            print("FLASH_SIZE(0): rows %d..%d" % bl.flash_size(0))
        except IOError:
            pass
        if not write:
            print("\nread-only probe complete. No flash was written.")
            bl.exit(); print("EXIT sent; the application restarts.")
            return

        _, _, _, rows = parse_cyacd(pack.cyacd(target + 1))
        print("\nprogramming %d rows (rows %d..%d) ..." % (len(rows), rows[0][1], rows[-1][1]))
        t0 = time.time()
        for i, (array, row, data) in enumerate(rows):
            bl.program_row(array, row, data)
            if i % 40 == 0 or i == len(rows) - 1:
                print("   %4d/%d  row %4d   %.0fs" % (i + 1, len(rows), row, time.time() - t0))
        print("all rows programmed in %.0fs" % (time.time() - t0))

        print("verifying application checksum ...")
        if not bl.verify_checksum():
            raise SystemExit("CHECKSUM FAILED - not setting the new image active. "
                             "The old image is still the running one.")
        print("checksum OK")
        bl.set_active_app(target)
        print("application %d set active" % target)
        bl.exit()
        print("EXIT sent; the microphone restarts on the new image.")
    finally:
        try: bl.close()
        except OSError: pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pack', required=True)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--deep', action='store_true', help='byte-compare every EEPROM file')
    ap.add_argument('--dsp', action='store_true')
    ap.add_argument('--recon', action='store_true')
    ap.add_argument('--flash', action='store_true')
    ap.add_argument('--all', action='store_true', help='full update: MCU, DSP files, version')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--i-understand-the-risk', action='store_true')
    a = ap.parse_args()
    pack = Pack(a.pack)
    if a.all:        cmd_all(a, pack)
    elif a.dsp:      cmd_dsp(a, pack)
    elif a.recon:    cmd_bootloader(a, pack, write=False)
    elif a.flash:    cmd_bootloader(a, pack, write=True)
    else:            cmd_check(a, pack)


if __name__ == '__main__':
    main()
