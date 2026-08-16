#!/usr/bin/env python3
"""Structural disassembler for SigmaDSP program RAM (40-bit words).

Analog Devices does not publish the SigmaDSP instruction set and no public
disassembler exists.  Everything below was derived from the MV7 program plus a
corpus of nine SigmaStudio-exported ADAU1701 programs whose block diagrams and
parameter names are known (the MCUdude/SigmaDSP examples).

WORD LAYOUT
    [39:27]  A    data / register address (13 bits; the top of the range is I/O)
    [26:16]  P    parameter RAM address   (8 bits on ADAU1701, 10 on ADAU1761)
    [15:0]   OP   control bitfield -- NOT an enumerated opcode

OPCODE BITS -- derived and verified
    bit 13 (0x2000)  engage the multiplier
    bit 9  (0x0200)  accumulate into the running sum instead of starting a new one
    bit 0             set on every ADAU1701 instruction, clear on every ADAU1761
                      instruction.  Part-specific constant, not semantic.

    Evidence: in the 3_Second_order_EQ reference, all 8 biquads (ground truth:
    4 stages x 2 channels) are exactly `0x2001` followed by four `0x2201`.
    0x2001 and 0x2201 differ only at bit 9.  A biquad is five multiply-
    accumulates, so the first tap starts the sum and the rest add to it.

MAC-CHAIN BIQUAD DETECTION
    A biquad is one `start` op followed by four `accumulate` ops.  On the MV7
    this finds 48 five-tap chains.  All 48 reference coefficients that differ
    between the 44.1 kHz and 48 kHz images -- i.e. all are filter coefficients --
    covering exactly 240 = 48 x 5 parameters in three banks of 16.  That agrees
    independently with the parameter-RAM analysis, which found three
    rate-dependent runs of 80, 80 and 81 words.

    Coefficient order differs by part:
        ADAU1701  base+3, base+4, base+0, base+1, base+2
        ADAU1761  base+2, base+4, base+0, base+1, base+3

NOT ESTABLISHED
    The remaining opcode bits.  Attribution by block type, from the graded
    reference series (each bit's first appearance):
        bare I/O path      bits 0, 9, 11, 13, 14, 15
        slewed volume      bits 6, 10, 12
        state-var filter   bits 1, 2, 3, 5, 7
        oscillator         bit 8
        never observed     bit 4
"""
import sys, collections

PARTS = {'adau1701': dict(pmask=0xFF,  fill=0x0000000001, start=0x2001, acc=0x2201, bit0=1),
         'adau1761': dict(pmask=0x3FF, fill=0x0000000000, start=0x2000, acc=0x2200, bit0=0)}

def decode(w, pmask):
    return (w >> 27) & 0x1FFF, (w >> 16) & pmask, w & 0xFFFF

IO_FLOOR = 8100          # A values at or above this are the memory-mapped I/O area

def classify(w, cfg, prev_chain):
    """Best-effort instruction class.  See the module docstring for evidence."""
    if w == cfg['fill'] or w == 0:
        return 'nop'
    op = w & 0xFFFF
    A = (w >> 27) & 0x1FFF
    if op == cfg['start']:  return 'mac.start'
    if op == cfg['acc']:    return 'mac.acc'
    if op == (0xE200 | cfg['bit0']): return 'store.dm'
    if op == (0x0200 | cfg['bit0']): return 'store.out'
    if op == cfg['bit0'] and A >= IO_FLOOR: return 'io'
    return 'unknown'

def mac_chains(W, cfg):
    """Find multiply-accumulate chains: one 'start' op then N 'accumulate' ops."""
    out, i = [], 0
    while i < len(W) - 1:
        if (W[i] & 0xFFFF) == cfg['start']:
            j = i + 1
            while j < len(W) and (W[j] & 0xFFFF) == cfg['acc']:
                j += 1
            if j > i + 1:
                out.append((i, j - i)); i = j; continue
        i += 1
    return out

def main():
    part = sys.argv[2] if len(sys.argv) > 2 else 'adau1761'
    cfg = PARTS[part]; pmask, fill = cfg['pmask'], cfg['fill']
    d = open(sys.argv[1], 'rb').read()
    W = [int.from_bytes(d[i:i+5], 'big') for i in range(0, len(d), 5)]
    n = max((i for i, w in enumerate(W) if w != fill), default=-1) + 1
    ch = mac_chains(W[:n], cfg)
    bq = [c for c in ch if c[1] == 5]
    inchain = {}
    for pc, k in ch:
        for m in range(k):
            inchain[pc + m] = (pc, k, m)
    print("; %s: %d words, %d before filler" % (part, len(W), n))
    print("; [39:27]=A addr  [%d:16]=P param  [15:0]=OP  (bit13=multiply, bit9=accumulate)"
          % (16 + pmask.bit_length() - 1))
    print("; MAC chains: %d total, %d of length 5 (biquads)" % (len(ch), len(bq)))
    print("; chain lengths: %s" % dict(sorted(collections.Counter(k for _, k in ch).items())))
    counts = collections.Counter(classify(w, cfg, None) for w in W[:n])
    tot = sum(v for k, v in counts.items() if k != 'nop')
    print("; classified: %s" % dict(counts))
    print("; coverage: %d of %d real instructions identified (%.0f%%)"
          % (tot - counts['unknown'], tot, 100.0 * (tot - counts['unknown']) / max(tot, 1)))
    print(";" + "-" * 72)
    for pc in range(n):
        w = W[pc]
        if w == fill:
            print(" %03X: %010X   nop" % (pc, w)); continue
        A, P, OP = decode(w, pmask)
        cls = classify(w, cfg, None)
        ann = "  " + cls
        if pc in inchain:
            h, k, m = inchain[pc]
            ann += "   ; %s tap %d/%d%s" % ("biquad" if k == 5 else "mac", m + 1, k,
                                             " coef=p%d" % P if m == 0 else "")
        print(" %03X: %010X   OP=%04X  A=%04X  P=%03X%s" % (pc, w, OP, A, P, ann))

if __name__ == '__main__':
    main()
