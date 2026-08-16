#!/usr/bin/env python3
"""Decode the MV7 DSP block-descriptor table (EEPROM 0x0200, mirrored in BLOCKDATA.dat).

Record layout:
    blockID(1) flag(1) kind(1)  then one or more entries
    entry: addr_hi addr_lo len(1) data[len]

`addr` is an ADAU1761 address: < 0x4000 is parameter RAM, >= 0x4000 is a control register.
The concatenated entry data is exactly what `getBlock <id>` returns.
"""
import sys, xml.etree.ElementTree as ET

def parse(ee, base=0x200, end=0x294):
    i, out = base, []
    while i < end and ee[i] != 0xFF:
        bid, flag, kind = ee[i], ee[i+1], ee[i+2]
        j, ents = i+3, []
        while j < end:
            addr = (ee[j] << 8) | ee[j+1]; ln = ee[j+2]
            ents.append((addr, ln, ee[j+3:j+3+ln])); j += 3 + ln
            if kind == 0x0A or len(ents) == 2: break
        out.append((bid, flag, kind, ents)); i = j
    return out

if __name__ == '__main__':
    ee = open(sys.argv[1], 'rb').read()
    names = {}
    if len(sys.argv) > 2:
        for b in ET.parse(sys.argv[2]).getroot().findall('block'):
            names[int(b.get('ID'), 16)] = b.get('name')
    print(" id  flag kind  addresses (space)            value              name")
    for bid, flag, kind, ents in parse(ee):
        addrs = ' '.join("0x%04X%s/%d" % (a, 'r' if a >= 0x4000 else 'p', l) for a, l, _ in ents)
        val = ''.join(d.hex() for _, _, d in ents)
        print("  %02X   %02X   %02X   %-28s %-18s %s"
              % (bid, flag, kind, addrs, val, names.get(bid, '')))
