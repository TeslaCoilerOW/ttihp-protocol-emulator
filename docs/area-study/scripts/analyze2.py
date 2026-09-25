#!/usr/bin/env python3
"""Area study v2: tabulate runs2/<mode>/<variant>/stat.txt and apply the utilization model.

Utilization model (calibrated on the local LibreLane 3.1.0.dev3 run of the design of
record, run2, 8x4; see docs/area-study.md section 2.4):
  placed_std = A * S + H * F
  S = replica TT-synthesis area (mode ll66), F = flip-flops in that netlist
  A = (583,689 - 74,686) / 462,710.3  (synthesis + design repair + CTS, per synthesized um2)
  H = 74,686 / 3,921                  (post-CTS hold buffers per flip-flop)
  U = (placed_std + macros) / core
Alternative (proportional) model: placed_std = 583,689 / 462,710.3 * S.
"""
import json, os, re, sys
W = os.environ['W']  # area-study work directory
R = W + '/runs2'
TIE = 7.2576
MAC = 8 * 236.80 * 64.36
CORE = {'8x4': (1724.16 - 12 * 0.48) * (710.64 - 2 * 3.78),
        '6x4': (1289.28 - 12 * 0.48) * (710.64 - 2 * 3.78)}
REF_S, REF_F = 462710.2914, 3921           # replica of run2's synthesis (runs2/snap_ll66)
PLACED, HOLD = 583689.0, 74686.1           # run2 step 37 std-cell area, hold-buffer growth
A = (PLACED - HOLD) / REF_S
H = HOLD / REF_F
KP = PLACED / REF_S


def st(path):
    s = open(path).read()
    a = float(re.search(r"Chip area for module '[^']*': ([\d.]+)", s).group(1))

    def cnt(p):
        return sum(int(m.group(1)) for m in re.finditer(r'^\s+(\d+)\s+\S+\s+' + p + r'\s*$', s, re.M))
    return dict(area=a,
                ff=cnt(r'sg13cmos5l_(?:s?dfrbpq?|sdfbbp)_\d'),
                tie=cnt(r'sg13cmos5l_tie(?:hi|lo)'),
                lat=cnt(r'sg13cmos5l_dl[hl]r?q?_\d'),     # dlhq/dlhrq/dlhr/dllrq/dllr, not dlygate
                icg=cnt(r'sg13cmos5l_s?lgcp_1'))


def util(S, F, core):
    two = (A * S + H * F + MAC) / CORE[core] * 100
    prop = (KP * S + MAC) / CORE[core] * 100
    return two, prop


def main():
    out = {'model': dict(A=A, H=H, KP=KP, macros=MAC, core=CORE), 'plain': {}, 'll66': {}, 'snap': {}}
    for mode in ('plain', 'll66'):
        d = f'{R}/{mode}'
        if not os.path.isdir(d):
            continue
        for v in sorted(os.listdir(d)):
            p = f'{d}/{v}/stat.txt'
            if os.path.exists(p):
                out[mode][v] = st(p)
    for v in ('snap_ll66', 'snap_plain', 'snap_ll', 'chk_base_plain'):
        p = f'{R}/{v}/stat.txt'
        if os.path.exists(p):
            out['snap'][v] = st(p)
    bl = out['ll66'].get('base', {}).get('area')
    bp = out['plain'].get('base', {}).get('area')
    print(f"model A={A:.4f} H={H:.2f} KP={KP:.4f} macros={MAC:.1f} core={ {k: round(c) for k, c in CORE.items()} }")
    print(f"{'variant':18s} {'plain':>10s} {'dP':>9s} {'TTsynth':>10s} {'dTT':>9s} {'ff':>5s} {'tie':>5s} "
          f"{'U8x4':>6s} {'U6x4':>6s} {'U6x4p':>6s}")
    for v, x in out['ll66'].items():
        p = out['plain'].get(v, {}).get('area', float('nan'))
        if x['ff'] and v.startswith(('blk', 'fl_')):
            continue
        u8, _ = util(x['area'], x['ff'], '8x4')
        u6, u6p = util(x['area'], x['ff'], '6x4')
        x.update(U8x4=u8, U6x4=u6, U6x4_prop=u6p)
        print(f"{v:18s} {p:10.1f} {(p - bp) if bp else 0:+9.1f} {x['area']:10.1f} {(x['area'] - bl) if bl else 0:+9.1f} "
              f"{x['ff']:5d} {x['tie']:5d} {u8:6.1f} {u6:6.1f} {u6p:6.1f}")
    print()
    for v in sorted(set(out['plain']) | set(out['ll66'])):
        if not v.startswith(('blk', 'fl_')):
            continue
        for mode in ('plain', 'll66'):
            x = out[mode].get(v)
            if x:
                print(f"{mode:5s} {v:20s} area={x['area']:9.1f} ff={x['ff']:4d} lat={x['lat']:4d} tie={x['tie']:4d} icg={x['icg']}")
    for v, x in out['snap'].items():
        print(f"snap  {v:20s} area={x['area']:9.1f} ff={x['ff']:4d} tie={x['tie']:4d}")
    json.dump(out, open(W + '/summary2.json', 'w'), indent=1)


if __name__ == '__main__':
    sys.exit(main())
