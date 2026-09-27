#!/usr/bin/env python3
"""Build a gold-vs-mutant miter of protocol_emulator_core for sequential equivalence.

usage: mk_miter.py GOLD.v MUT.v OUT.v

Both cores are write_verilog output of base.il (gold unmutated, mut with one
`mutate` applied). Each of the eight SRAM macro instances is cut out of both
copies: its A_DOUT net becomes a module input that the miter drives from ONE
free input shared by the two copies, and its inputs (MEN, WEN, REN, ADDR, DIN)
become module outputs. The miter asserts, every cycle after the power-up reset,

  (strict)   uo_out, uio_out, uio_oe equal, and the SRAM inputs equal
             (MEN; with MEN: WEN, REN, ADDR; with MEN and WEN: DIN);
  (contract) the same, except that uo_out[3:0] (the read nibble) is compared
             only while read-valid (uo_out[5]) is high (docs/isa.md: unused
             read-nibble values while read-valid is low are unspecified).

Since equal SRAM input histories give equal SRAM outputs, a proof with the
shared free A_DOUT (any value on every cycle) implies equality with the real
macros for any memory contents. All flip-flops start at zero and rst_n is
forced low on the first edge (a power-up reset). Define CONTRACT for the
contract property. MITER_MODE=SHARED (v2): the SRAM read addresses may differ;
the mutant then reads an independent free value (sram_dout_m) on the cycle
after a read whose enable or address differs from gold's, and only the write
side (enable, address, data) is asserted equal. Without it (v1) every SRAM
input, reads included, is asserted equal. MITER_MODE=SHARED,REG (v3, for
k-induction): the gold SRAM output is a register that changes only on a read
and repeats the last word when the last address is read again with no write in
between (the macro's registered read); other reads return a free value. Define
EXTRA to include extra.vh (invariant assertions for k-induction).
"""
import os
import re
import sys

INST = re.compile(r"  RM_IHPSG13_1P_64x16_c2 (\w+) \(\n(.*?)\n  \);\n", re.S)
PORT = re.compile(r"\.(A_\w+)\((.*?)\)(?:,)?$")


REG = re.compile(r"^  reg (?:\[(\d+):0\] )?(\w+)(?: \[(\d+):0\])?;$", re.M)


def state_regs(src: str) -> list[tuple[str, int, str]]:
    """(name, width, verilog expression) of every register and memory word, in declaration order."""
    out = []
    for m in REG.finditer(src):
        width = int(m.group(1)) + 1 if m.group(1) else 1
        if m.group(3):
            for word in range(int(m.group(3)) + 1):
                out.append((f"{m.group(2)}_w{word}", width, f"{m.group(2)}[{word}]"))
        else:
            out.append((m.group(2), width, m.group(2)))
    return out


def cut(src: str, name: str) -> tuple[str, list[str]]:
    state = os.environ.get("MITER_STATE") == "1"
    extra = ", state" if state else ""
    src = src.replace("module protocol_emulator_core(uio_in, ena, rst_n, clk, ui_in, uo_out, uio_out, uio_oe);",
                      f"module {name}(uio_in, ena, rst_n, clk, ui_in, uo_out, uio_out, uio_oe, sram_dout, sram_in{extra});", 1)
    if f"module {name}(" not in src:
        raise SystemExit("module header not found")
    insts = []
    body = []

    def repl(m: re.Match) -> str:
        inst = m.group(1)
        ports = {}
        for line in m.group(2).splitlines():
            pm = PORT.match(line.strip())
            if not pm:
                raise SystemExit(f"cannot parse port line {line!r}")
            ports[pm.group(1)] = pm.group(2)
        k = len(insts)
        insts.append(inst)
        # sram_in[k] = {MEN, WEN, REN, ADDR[5:0], DIN[15:0]} (25 bits)
        body.append(f"  assign {ports['A_DOUT']} = sram_dout[{16 * k + 15}:{16 * k}];\n")
        body.append(f"  assign sram_in[{25 * k + 24}:{25 * k}] = {{ {ports['A_MEN']}, {ports['A_WEN']}, "
                    f"{ports['A_REN']}, {ports['A_ADDR']}, {ports['A_DIN']} }};\n")
        return ""

    src = INST.sub(repl, src)
    if len(insts) != 8:
        raise SystemExit(f"expected 8 SRAM instances, found {len(insts)}")
    decl = "  input [127:0] sram_dout;\n  output [199:0] sram_in;\n"
    if state:
        regs = state_regs(src)
        total = sum(w for _, w, _ in regs)
        decl += f"  output [{total - 1}:0] state;\n"
        body.append(f"  assign state = {{ {', '.join(e for _, _, e in reversed(regs))} }};\n")
    src = src.replace("  input [7:0] uio_in;\n", decl + "  input [7:0] uio_in;\n", 1)
    idx = src.rfind("endmodule")
    src = src[:idx] + "".join(body) + src[idx:]
    return src, insts


def main() -> None:
    gold_v, mut_v, out = sys.argv[1:4]
    g, gi = cut(open(gold_v).read(), "pe_gold")
    m, mi = cut(open(mut_v).read(), "pe_mut")
    if gi != mi:
        raise SystemExit("SRAM instance order differs")
    shared = "SHARED" in os.environ.get("MITER_MODE", "")
    regmem = "REG" in os.environ.get("MITER_MODE", "")
    lines = ["  wire [127:0] gdout;"] if regmem else []
    for k in range(8):
        b = 25 * k
        men, wen, ren = b + 24, b + 23, b + 22
        # writes must match exactly (they define the memory contents both copies read)
        lines.append(f"  wire w_ok{k} = (gi[{men}] & gi[{wen}]) == (mi[{men}] & mi[{wen}]) && "
                     f"(!(gi[{men}] & gi[{wen}]) || (gi[{b + 21}:{b + 16}] == mi[{b + 21}:{b + 16}] && "
                     f"gi[{b + 15}:{b}] == mi[{b + 15}:{b}]));")
        # a read by the mutant returns the gold data only if it read the same address
        lines.append(f"  wire r_same{k} = (gi[{men}] & gi[{ren}]) == (mi[{men}] & mi[{ren}]) && "
                     f"(!(gi[{men}] & gi[{ren}]) || gi[{b + 21}:{b + 16}] == mi[{b + 21}:{b + 16}]);")
        # the macro holds DOUT when it does not read: equal outputs stay equal only while
        # neither copy reads, or both read the same address
        lines.append(f"  reg same{k} = 1'b1;")
        lines.append(f"  always @(posedge clk) same{k} <= (gi[{men}] & gi[{ren}]) | (mi[{men}] & mi[{ren}]) "
                     f"? r_same{k} : same{k};")
        if regmem:
            # registered read abstraction of the macro (models/RM_IHPSG13_1P_core_behavioral.v): DOUT is a
            # register that changes only on a read; a read of the address read last, with no write since,
            # returns the same word; any other read returns a free value (any memory contents)
            lines += [f"  reg [15:0] gd{k} = 16'd0; reg [5:0] la{k} = 6'd0; reg lv{k} = 1'b0;",
                      f"  wire g_rd{k} = gi[{men}] & gi[{ren}], g_wr{k} = gi[{men}] & gi[{wen}];",
                      f"  always @(posedge clk) begin",
                      f"    if (g_rd{k}) begin gd{k} <= (lv{k} && la{k} == gi[{b + 21}:{b + 16}]) ? gd{k} : "
                      f"sram_dout[{16 * k + 15}:{16 * k}]; la{k} <= gi[{b + 21}:{b + 16}]; lv{k} <= 1'b1; end",
                      f"    else if (g_wr{k}) lv{k} <= 1'b0;",
                      f"  end",
                      f"  assign gdout[{16 * k + 15}:{16 * k}] = gd{k};"]
            src = f"gd{k}"
        else:
            src = f"sram_dout[{16 * k + 15}:{16 * k}]"
        lines.append(f"  assign mdout[{16 * k + 15}:{16 * k}] = same{k} ? {src} : "
                     f"sram_dout_m[{16 * k + 15}:{16 * k}];")
    w_ok = " && ".join(f"w_ok{k}" for k in range(8))
    r_ok = " && ".join(f"r_same{k}" for k in range(8))
    state = os.environ.get("MITER_STATE") == "1"
    if state:
        regs = state_regs(open(gold_v).read())
        total = sum(w for _, w, _ in regs)
        off = 0
        smap = [f"  wire [{total - 1}:0] gs, ms;"]
        for nm, w, _ in regs:
            smap.append(f"  wire [{w - 1}:0] g_{nm} = gs[{off + w - 1}:{off}];")
            smap.append(f"  wire [{w - 1}:0] m_{nm} = ms[{off + w - 1}:{off}];")
            off += w
        lines += smap
        with open(out + ".regs", "w") as fh:
            fh.write("\n".join(f"{nm} {w}" for nm, w, _ in regs) + "\n")
    body = "\n".join(lines)
    miter = f"""
module miter(input clk, input rst_n, input ena, input [7:0] ui_in, input [7:0] uio_in,
             input [127:0] sram_dout, input [127:0] sram_dout_m);
  reg started = 1'b0;
  always @(posedge clk) started <= 1'b1;
`ifdef EXTRA
  wire rst_eff = rst_n;  // k-induction: the all-zero initial state is the reset state
`else
  wire rst_eff = started & rst_n;
`endif
  wire [7:0] guo, gout, goe, muo, mout, moe;
  wire [199:0] gi, mi;
  wire [127:0] mdout;
{body}
  pe_gold g(.uio_in(uio_in), .ena(ena), .rst_n(rst_eff), .clk(clk), .ui_in(ui_in),
            .uo_out(guo), .uio_out(gout), .uio_oe(goe), .sram_dout({"gdout" if regmem else "sram_dout"}), .sram_in(gi){", .state(gs)" if state else ""});
  pe_mut m(.uio_in(uio_in), .ena(ena), .rst_n(rst_eff), .clk(clk), .ui_in(ui_in),
           .uo_out(muo), .uio_out(mout), .uio_oe(moe), .sram_dout({"sram_dout" if not shared else "mdout"}), .sram_in(mi){", .state(ms)" if state else ""});
`ifdef CONTRACT
  wire uo_ok = guo[7:4] == muo[7:4] && (!guo[5] || guo[3:0] == muo[3:0]);
`else
  wire uo_ok = guo == muo;
`endif
  wire sram_ok = {w_ok if shared else w_ok + " && " + r_ok};
`ifdef EXTRA
`include "extra.vh"
`endif
`ifdef EXTRA
  always @* assert (uo_ok && gout == mout && goe == moe && sram_ok);
`else
  always @* if (started) assert (uo_ok && gout == mout && goe == moe && sram_ok);
`endif
endmodule
"""
    with open(out, "w") as fh:
        fh.write(g + "\n" + m + "\n" + miter)


if __name__ == "__main__":
    main()
