# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Setup, hold and sampling margins of the lockstep PIO program.

The schedule comes from the program itself (pathcheck.check_program): a
frame of FRAME_CYCLES system clocks, ui_in driven at slot 3, the rising edge
at slot 0, uo_out read at slot 2. With t = 1/f_sys and the rising edge on the
RP2's clock pin at time 0 (the end of the slot-0 cycle):

* ui_in changes at -3t and +3t: 3t of set-up before the edge and 3t of hold
  after the previous one, both at the RP2's pins;
* the slot-2 instruction reads the input synchroniser, whose first flop
  sampled the pads at time 0 (the system-clock edge that raises clk): uo_out
  must be valid at the RP2's pins FRAME_CYCLES * t after the previous rising
  edge; the chip cannot change it before 0, since every path from the clock
  pin back to the RP2 has positive delay.

Chip-side requirements at the project boundary come from the post-route
static timing of the design of record (tools/sta re-analysis of the p018
layout at 15 ns, the layout of the official d76f1cc build: Slurm job 24089033,
docs/timing-closure.md section 10.5; per-class worst slacks from
tools/sta/pe_extra.tcl). The flow's SDC puts every input and output delay at
X = 20% of the period (3 ns at 15 ns) and a 0.25 ns clock uncertainty, so,
per corner, with T = 15 ns:

    set-up of ui_in before clk    t_su  = T - X - slack(setup, input to register)
    hold of ui_in after clk       t_h   = X - slack(hold, input to register)
    clk to uo_out (max)           t_co  = T - X - slack(setup, register to output)

(the slacks already include the uncertainty, so these are upper bounds).
The input-to-register hold slack is the worst over all inputs (uio_in and
ui_in paths), so t_h is conservative for ui_in.

What is outside the chip is an assumption, taken from the Tiny Tapeout
GPIO page ("Multiplexer measurements"), which describes the sky130 pads (the
IHP pads have no published figures): a "worst round trip latency" through
the pads and the multiplexer of 20 ns, and a "Delay variance between
different IO pins" of "less than 2ns". Both were measured on a single Tiny
Tapeout 3.5 die at about 22 degrees C and are published for reference only.
margins() combines them with the chip's timing at the requested corner, so
the slow-corner figures are not a slow-corner analysis of the whole path.
RP2 pad delays and board traces are not included: the sampling margin below
is what is left for them.
"""

import os
import sys

HOST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOST not in sys.path:
    sys.path.insert(0, HOST)

from fractions import Fraction as Q  # noqa: E402

from pe_host.ports import pio_lockstep as pl  # noqa: E402

from . import pathcheck  # noqa: E402

# tools/sta, p018 at 15 ns (job 24089033): worst slack per class, ns
STA_PERIOD = Q(15)
STA_IO_DELAY = Q(3)
STA = {
    "slow": {"setup_in2reg": Q("2.789646"), "hold_in2reg": Q("1.259909"),
             "setup_reg2out": Q("5.944791"), "hold_reg2out": Q("5.427528")},
    "typ": {"setup_in2reg": Q("6.175710"), "hold_in2reg": Q("1.848837"),
            "setup_reg2out": Q("8.050797"), "hold_reg2out": Q("4.478095")},
    "fast": {"setup_in2reg": Q("8.148133"), "hold_in2reg": Q("2.154488"),
             "setup_reg2out": Q("9.277676"), "hold_reg2out": Q("3.914381")},
}
# tinytapeout.com/specs/gpio, "Multiplexer measurements" (sky130; one TT3.5
# die at about 22 degrees C, for reference only)
TT_ROUND_TRIP = Q(20)     # ns, worst round trip latency
TT_PIN_SKEW = Q(2)        # ns, delay variance between pins: less than 2 ns
F_SYS = (125000000, 144000000, 150000000)


def chip_requirements(corner):
    s = STA[corner]
    return {
        "t_su": STA_PERIOD - STA_IO_DELAY - s["setup_in2reg"],
        "t_h": STA_IO_DELAY - s["hold_in2reg"],
        "t_co": STA_PERIOD - STA_IO_DELAY - s["setup_reg2out"],
        "t_co_min": s["hold_reg2out"] - STA_IO_DELAY,
    }


def schedule():
    """(frame, drive slot, sample slot) from the program's path check."""
    report = pathcheck.check_program()
    if report["errors"]:
        raise ValueError(report["errors"])
    slots = dict(report["slots"])
    drive = {slots[pc] for pc in report["drive_instructions"]}
    sample = {slots[pc] for pc in report["sample_instructions"]}
    if len(drive) != 1 or len(sample) != 1:
        raise ValueError("drive or sample slot not unique: %r %r" % (drive, sample))
    return pl.FRAME_CYCLES, drive.pop(), sample.pop()


def margins(f_sys, corner="slow", skew=TT_PIN_SKEW, round_trip=TT_ROUND_TRIP):
    """Margins in ns (Fractions) for one system clock and corner."""
    frame, drive, sample = schedule()
    t = Q(1000000000, f_sys)                       # ns per system clock
    edge = 1                                       # the slot-0 cycle ends at the edge
    setup = ((frame - drive) % frame) * t          # ui change -> next rising edge
    hold = (drive - 0) * t                         # rising edge -> next ui change
    # the slot-s instruction reads the level sampled at the start of cycle s-1,
    # i.e. (s - 1 - edge) cycles after the rising edge (0 for slot 2)
    sample_offset = (sample - 1 - edge) * t
    window = frame * t + sample_offset             # previous rising edge -> sample
    req = chip_requirements(corner)
    return {
        "f_sys": f_sys,
        "project_clock_hz": Q(f_sys, frame),
        "t_ns": t,
        "setup_at_pins": setup,
        "hold_at_pins": hold,
        "sample_window": window,
        "setup_margin": setup - skew - req["t_su"],
        "hold_margin": hold - skew - req["t_h"],
        "sample_margin_before_rp2_and_board": window - round_trip - req["t_co"],
        "max_skew_setup": setup - req["t_su"],
        "requirements": req,
    }


def table():
    lines = ["| f_sys | project clock | corner | setup at pins | setup margin | hold margin | "
             "sampling window | sampling margin |", "|---|---|---|---|---|---|---|---|"]
    for f_sys in F_SYS:
        for corner in ("slow", "typ", "fast"):
            m = margins(f_sys, corner)
            lines.append("| %.0f MHz | %.3f MHz | %s | %.3f ns | %.3f ns | %.3f ns | %.3f ns | %.3f ns |"
                         % (f_sys / 1e6, float(m["project_clock_hz"]) / 1e6, corner,
                            float(m["setup_at_pins"]), float(m["setup_margin"]),
                            float(m["hold_margin"]), float(m["sample_window"]),
                            float(m["sample_margin_before_rp2_and_board"])))
    return "\n".join(lines)


if __name__ == "__main__":
    print(table())
    for corner in ("slow", "typ", "fast"):
        req = chip_requirements(corner)
        print(corner, {k: float(v) for k, v in req.items()})
