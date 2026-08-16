import re, json, sys
NAMES = {
0x4000:'CLOCK_CONTROL',0x4002:'PLL_CONTROL(6B)',0x4008:'DIGMIC_JACKDETECT',
0x4009:'REC_POWER_MGMT',0x400a:'REC_MIXER_LEFT0',0x400b:'REC_MIXER_LEFT1',
0x400c:'REC_MIXER_RIGHT0',0x400d:'REC_MIXER_RIGHT1',0x400e:'LEFT_DIFF_INPUT_VOL',
0x400f:'RIGHT_DIFF_INPUT_VOL',0x4010:'MICBIAS',0x4011:'ALC_CTRL0',0x4012:'ALC_CTRL1',
0x4013:'ALC_CTRL2',0x4014:'ALC_CTRL3',0x4015:'SERIAL_PORT0',0x4016:'SERIAL_PORT1',
0x4017:'CONVERTER0',0x4018:'CONVERTER1',0x4019:'ADC_CONTROL',0x401a:'LEFT_INPUT_DIGITAL_VOL',
0x401b:'RIGHT_INPUT_DIGITAL_VOL',0x401c:'PLAY_MIXER_LEFT0',0x401d:'PLAY_MIXER_LEFT1',
0x401e:'PLAY_MIXER_RIGHT0',0x401f:'PLAY_MIXER_RIGHT1',0x4020:'PLAY_LR_MIXER_LEFT',
0x4021:'PLAY_LR_MIXER_RIGHT',0x4022:'PLAY_MIXER_MONO',0x4023:'PLAY_HP_LEFT_VOL',
0x4024:'PLAY_HP_RIGHT_VOL',0x4025:'PLAY_LINE_LEFT_VOL',0x4026:'PLAY_LINE_RIGHT_VOL',
0x4027:'PLAY_MONO_OUTPUT_VOL',0x4028:'POP_CLICK_SUPPRESS',0x4029:'PLAY_POWER_MGMT',
0x402a:'DAC_CONTROL0',0x402b:'DAC_CONTROL1',0x402c:'DAC_CONTROL2',0x402d:'SERIAL_PORT_PAD',
0x402f:'CONTROL_PORT_PAD0',0x4030:'CONTROL_PORT_PAD1',0x4031:'JACK_DETECT_PIN',
0x4036:'DEJITTER',0x40c0:'SERIAL_IN/OUT_ROUTE',0x40e9:'(0x40E9)',0x40eb:'DSP_SAMPLING_RATE',
0x40f2:'SERIAL_INPUT_ROUTE',0x40f3:'SERIAL_OUTPUT_ROUTE',0x40f4:'DSP_SLEW/FDSP',
0x40f5:'DSP_ENABLE',0x40f6:'DSP_RUN',0x40f7:'(0x40F7)',0x40f8:'SERIAL_SAMPLING_RATE',
0x40f9:'CLK_ENABLE0',0x40fa:'CLK_ENABLE1',
}
def section(name, path='DSP-MV7.dat'):
    buf=[];grab=False
    for ln in open(path):
        ln=ln.strip()
        if ln.startswith('update '+name): grab=True; continue
        if grab and ln.startswith('END'): break
        if grab and re.fullmatch(r'[0-9A-F]+',ln): buf.append(ln)
    return bytes.fromhex(''.join(buf))
def records(d):
    lens=[];i=0
    while True:
        v=(d[i]<<8)|d[i+1]; i+=2
        if v==0: break
        lens.append(v)
    out=[];off=i
    for L in lens:
        rec=d[off:off+L]; out.append(((rec[0]<<8)|rec[1], rec[2:])); off+=L
    return out, d[off:]
prog={}
for nm in ('MV7DSP44K.hex','MV7DSP48K.hex'):
    recs,trail=records(section(nm))
    print("### %s   (trailer/version %s)"%(nm, trail.hex()))
    for a,p in recs:
        if a>=0x4000:
            for k,b in enumerate(p):
                addr=a+k
                prog.setdefault(nm,{})[addr]=b
                print("   0x%04X = 0x%02X   %s"%(addr,b,NAMES.get(addr,'')))
        else:
            kind='PROGRAM RAM (5-byte words)' if 0x0800<=a<0x1000 else 'PARAM/DATA RAM (4-byte words)'
            n = len(p)//5 if 0x0800<=a<0x1000 else len(p)//4
            print("   0x%04X <- %d bytes  = %d words  [%s]"%(a,len(p),n,kind))
    print()
json.dump({k:{('%04X'%a):v for a,v in d.items()} for k,d in prog.items()},
          open('/home/schlarpc/re-shell/tmp/fw/dsp_programmed_regs.json','w'), indent=1)
