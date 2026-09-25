#!/usr/bin/env python3
"""Hand classification of the random survivor sample (classify_sample.txt, seed 20270118).

usage: sample_classification.py CAMP_DIR OUT.tsv

Merges the classification below with the automatic evidence (formal
equivalence, RIP analysis, deep random stage) from evidence.py and writes a TSV.

Classes:
  EQ-proven   Yosys equiv_simple + equiv_induct proved the mutant equivalent
  EQ-manual   equivalent in every reachable state; the argument is given (the
              induction proof fails because it needs an invariant, e.g. "the
              mutated hold path is only used while the engine is inactive")
  GAP         killable; the stimulus that would kill it is given
Engine numbers come from engine_map.py (the Hardcaml name suffixes of
per-engine registers do not follow the engine index). In the notes, "hold path" is the else branch of a Hardcaml `if`, taken when
the engine is not active (halted, faulted, or on a start/stop/clear edge).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

C = {
 "98":  ("GAP", "blocked_cycles of engine 1 is left at 2 instead of 0 after ALU/SHL/SHR/JZ/NOT/TIME (opcodes 20-28). Only visible as a WAITPIN/WAITEVENT timeout two samples early. Kill: an ALU instruction immediately followed by a WAITPIN on a pin that never matches, with a small LIMIT; the fault-3 edge moves."),
 "174": ("GAP", "blocked_cycles of engine 1: bit 0 flips once the count has bit 13 set. Needs a bounded wait of >= 8192 cycles; no test blocks that long. Kill: LIMIT 0x2108 then WAITPIN on a static pin; fault 3 arrives at a different cycle."),
 "185": ("GAP", "wait_timer countdown of engine 2: bit 4 flips while bit 21 is set, i.e. only for WAIT n with n >= 2^21. Kill: WAIT 0x200010 followed by a pin toggle (about 2.1 M cycles of simulation), or an inductive property timer' = timer - 1."),
 "195": ("GAP", "Same as 174 for engine 0 (blocked_cycles): bit 4 flips when bit 13 is set; needs a bounded wait of >= 8192 cycles. The mutated count oscillates by 16, so LIMIT must not be a multiple of 32 (0x2100 does not kill it, 0x2108 does)."),
 "268": ("GAP", "repeat_count_0 (engine 3), D input: bit 2 flips when bit 3 is set. Needs COUNT n with bit 3 set (n >= 8) and a LOOP on engine 3; iteration count changes. Never happened in the suite."),
 "271": ("GAP", "repeat_count of engine 1 stuck below 8192 (bit 13 forced 0). Kill: COUNT 0x2000 + LOOP on engine 1; loop exits early (visible in pin timing or the completed-instruction count)."),
 "277": ("EQ-proven", "Bit 20 of pc+1 on the instruction-execution path, which is only taken while pc < image_length (a 16-bit register), so the bit is always 0."),
 "279": ("EQ-manual", "Clears blocked_cycles bit 22 on the XFER/FAULT hold path. blocked_cycles is nonzero only while a WAITPIN/WAITEVENT is issuing (every other completed instruction resets it; fault is followed by START, which resets it), so the path never sees a nonzero value."),
 "297": ("GAP", "completed_instructions of engine 1: bit 17 flips when bit 20 is set; needs >= 2^20 completed instructions before READ_SELECT 5. Long run (about 1 M cycles) or an inductive counter property."),
 "313": ("GAP", "completed_instructions of engine 1: bit 1 flips when bit 23 is set; needs >= 2^23 instructions before READ_SELECT 5. Practical only with a formal counter property (count' = count + finished)."),
 "396": ("GAP", "completed_instructions of engine 3: bit 10 forced 0 on one opcode's update path. Needs >= 1024 completed instructions including that opcode, then READ_SELECT 5."),
 "423": ("GAP", "completed_instructions_0 (engine 2): bit 15 cleared on the hold path while halted; the count is readable while halted. Kill: run >= 32768 instructions, HALT, READ_SELECT 5."),
 "436": ("GAP", "blocked_cycles of engine 1 left at 16 after TIME (and FAULT-class paths). Only visible as a WAITPIN/WAITEVENT timeout 16 samples early. Kill: TIME then a WAITPIN that times out."),
 "451": ("GAP", "repeat_count_0 (engine 3): bit 12 flips whenever bit 6 is set. Kill: COUNT 72 + LOOP on engine 3; iteration count wrong. The suite never used COUNT >= 64 on engine 3."),
 "478": ("GAP", "blocked_cycles_2 (engine 2) left at 32 after opcodes 21-28. Kill: XOR/AND/OR/SHL/SHR/JZ/NOT/TIME immediately followed by a WAITPIN that times out."),
 "565": ("EQ-manual", "Reset value of image_loaded_2 becomes 1. The value is only used while image_writing is set, which only BEGIN sets, and BEGIN zeroes image_loaded; so the reset value is never used."),
 "570": ("EQ-manual", "BEGIN sets image_length_0 to 0x100 instead of 0. The length is only used by a running engine, and START needs a COMMIT, which overwrites it."),
 "696": ("GAP", "OWN overlap check ignores engine 1 owning pin 5. Kill: engine 1 owns pin 5; another engine then OWNs pin 5; must be rejected with the sticky host fault and unchanged ownership."),
 "747": ("EQ-proven", "Read-nibble counter compare for case item 0: flipping bit 0 only when bit 2 is set can never make the value 0."),
 "852": ("GAP", "image_length_1 (engine 1): bit 7 of the register output is XORed with bit 6, and the hold path feeds the mutated value back, so a committed length of exactly 64 alternates 64/192 every cycle. Kill: a full 64-word image that falls through on a cycle where the length reads 192 (fault 2 expected at PC 64; the mutant runs on). With 64 NOPs the fall-through lands on a 64 cycle; a WAIT 1 prefix shifts it."),
 "952": ("GAP", "ROUTE word count of source engine 2 truncated below 4096 (bit 12 forced 0). Kill: ROUTE count 4097 from engine 2 and stream more than 1 word; the mutant disables the route after 1 word."),
 "979": ("GAP", "DMA round-robin cursor bit 0 forced to 1 on its hold path (differs in 293,756 cycles). Only matters when two routes compete on the same edge. Kill: two enabled routes whose sources both hold a word and whose destinations accept on the same edge; check grant order."),
 "989": ("GAP", "route_remaining of source engine 0: bit 3 flips when bit 5 is set. Kill: ROUTE count >= 32 from engine 0 and transfer until the descriptor expires; the number of words moved changes."),
 "1008": ("GAP", "Host TX write priority over DMA for route 0's destination is lost (grant not inhibited). Kill: host window-2 write to engine D on the same edge as a DMA word from route source 0 to D."),
 "1040": ("EQ-proven", "No-op: the reset value of route_destination_3 is the constant 2'b00, whose bit 0 is already 0."),
 "1112": ("GAP", "Mover destination-3 decode. Killed by the deep random stage (256 cases from a new seed): the 64-case default budget is too small."),
 "1152": ("GAP", "FLUSH no longer disables route 1 when the flushed engine is its source or destination. Kill: enable route 1->D, FLUSH engine 1 (or D) while halted, then check no DMA word moves."),
 "1222": ("EQ-proven", "Compare-with-zero in the trigger/event path: bit 2 flips only when bit 4 is set, and the value is nonzero either way."),
 "1317": ("EQ-proven", "Compare-with-zero in the trigger/event path: bit 0 flips only when bit 6 is set, and the value is nonzero either way."),
 "1342": ("EQ-proven", "Case-item compare (item 29) in engine 1's opcode-validity decode; proved by equiv_induct."),
 "1345": ("EQ-proven", "No-op: the bound is the constant 4, whose bit 2 is already 1."),
 "1356": ("GAP", "Ownership-masked pin decode on engine 0's event path; in the suite it changed logical_output_2 for 8,461 cycles without reaching a pin. Killed by the deep random stage."),
 "1371": ("EQ-proven", "No-op: case-item constant 11 (LOOP) in engine 1's validity decode already has bit 2 = 0."),
 "1416": ("GAP", "Engine 1 operand check bc_zero forced true: PUSH/NOT/TIME with nonzero b or c fields are executed instead of faulting with code 1. Kill: engine 1 runs e.g. TIME x with c != 0 and must fault 1."),
 "1521": ("GAP", "logical_output_2 (engine 0) XFER data-pin update: when the data pin is 3 and the bit is 1, bit 4 also toggles (differs 5,155 cycles). Kill: XFER on engine 0 with data on pin 3 while engine 0 owns and drives pin 4."),
 "1577": ("EQ-manual", "logical_enable (engine 3) bit 3 toggles on the hold path, which is only used while the engine is inactive; its pins are masked by run/fault then, and START/STOP zero the register (differs 114,442 cycles, never visible)."),
 "1634": ("EQ-manual", "Same as 1577 for logical_enable_2 (engine 0) bit 7."),
 "1694": ("GAP", "logical_output_0 (engine 2) XFER output update corrupts pin 0 (clears it, or keeps it when the data pin is 0). Kill: SPI-style XFER with drive on engine 2 while engine 2 also owns and drives pin 0. Killed by the deep random stage."),
 "1753": ("GAP", "transfer_period (engine 2): during a transfer the period register alternates between b and b^32 every cycle when bit 7 is set; half-periods >= 128 are never used. Kill: XFER with an odd half-period >= 128 (201) on an owned, enabled clock pin; with an even period every reload lands on the same phase."),
 "1761": ("EQ-manual", "transfer_tick_2 (engine 0) bit 2 cleared on the hold path while the engine is inactive; a transfer only runs while active, STOP zeroes transfer_edges and START zeroes the tick."),
 "1943": ("EQ-proven", "transfer_edges_2 update path; proved by equiv_induct."),
 "1978": ("EQ-manual", "Bit 1 of the host write-nibble shift register stuck at 1. The assembled word is {new nibble, reg[31:4]}, so reg[3:0] is shifted out and never read (differs 282,346 cycles, never visible)."),
 "2121": ("GAP", "DMA word data: bit 3 flips when bit 15 is set, corrupting routed words in the destination TX FIFO (FIFO words differ). Kill: route words with bit 15 set and have the destination engine PULL and transmit them on an owned pin, or route them back to an RX FIFO the host reads."),
 "2147": ("EQ-proven", "FIFO pointer update enable: forcing one AND operand to 1 is harmless because the other operand implies it; proved by equiv_induct."),
 "2245": ("EQ-manual", "x_2 (engine 0) bit 3 toggles on the hold path while inactive; x is not host-readable and START resets it."),
 "2279": ("GAP", "SHR result written to rx_0 (engine 1): bit 15 forced 0. Kill: rx = 0x80000000 (LOAD 1, SHL 31), SHR 16, PUSH; the host reads 0x8000."),
 "2313": ("EQ-proven", "Compare of engine 3's 2-bit register select with 0: bit 1 flips only when bit 0 is set, and the value is nonzero either way."),
 "2344": ("EQ-manual", "tx_0 (engine 1) bit 6 cleared on the hold path while inactive; tx is not host-readable and START resets it."),
}


def main() -> None:
    camp, out = Path(sys.argv[1]), Path(sys.argv[2])
    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(here / "evidence.py"), str(camp), str(camp / "classify_sample.txt"),
                        "--json", f"{tmp}/ev.json"], check=True, stdout=subprocess.DEVNULL)
        ev = json.loads(Path(f"{tmp}/ev.json").read_text())
    rows = ["id\tregion\tmutation\tclass\tequiv\trip\tdeep\tdirected\tdiverged_state\tnote"]
    for r in ev:
        cls, note = C[r["id"]]
        if cls == "EQ-proven" and r["equiv"] != "equivalent":
            raise SystemExit(f"{r['id']}: marked EQ-proven but equiv={r['equiv']}")
        if r["equiv"] == "equivalent" and cls != "EQ-proven":
            raise SystemExit(f"{r['id']}: proven equivalent but classified {cls}")
        if cls.startswith("EQ") and "killed" in (r["deep"], r["directed"]):
            raise SystemExit(f"{r['id']}: classified equivalent but killed by the deep stage")
        mut = f"{r['mode']} {r['port']}" + (f" ctrl {r['ctrlbit']}" if r["ctrlbit"] != "-" else "")
        div = ",".join(f"{k}:{v}" for k, v in r.get("rip_state", {}).items())
        rows.append("\t".join([r["id"], r["region"], mut, cls, r["equiv"], r["rip"], r["deep"], r["directed"], div, note]))
    out.write_text("\n".join(rows) + "\n")
    counts = {}
    for line in rows[1:]:
        c = line.split("\t")[3]
        counts[c] = counts.get(c, 0) + 1
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
