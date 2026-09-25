# Copied verbatim (apart from this header) from the asic-lab monorepo:
#   projects/protocol-emulator/python/protocol_emulator/host.py @ commit 18676a4
# Independent pure-Python ISA2 reference (stdlib only). Keep in sync with
# docs/isa.md; any semantic change must update RTL, assembler and model.
# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""Synchronous nibble host and reproducible stimulus recording."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .reference import Outputs, Reference


@dataclass(frozen=True)
class Cycle:
    ui: int
    pins: int
    reset: bool
    enabled: bool
    expected: Outputs


class Host:
    """Drive handshakes against the model and retain the exact RTL stimulus.

    Read nibbles and ready/valid are sampled before the rising edge. ``cycles``
    stores outputs immediately after that edge, suitable for HDL differential
    replay. Pin suppliers observe the cycle number before an edge.
    """

    def __init__(self, machine: Reference, pins: int | Callable[[int], int] = 0) -> None:
        self.machine = machine
        self.pins = pins
        self.cycles: list[Cycle] = []
        self.window = 0

    def cycle(self, ui: int | None = None, *, reset: bool = False,
              enabled: bool = True) -> Outputs:
        if ui is None:
            ui = self.window << 6
        pin_value = self.pins(len(self.cycles)) if callable(self.pins) else self.pins
        sampled = self.machine.outputs(ui, reset=reset, enabled=enabled)
        expected = self.machine.tick(ui, pin_value, reset=reset, enabled=enabled)
        self.cycles.append(Cycle(ui, pin_value, reset, enabled, expected))
        return sampled

    def idle(self, count: int = 1) -> None:
        for _ in range(count):
            self.cycle()

    def write(self, window: int, word: int, *, timeout: int = 100000) -> None:
        if window not in (0, 1, 2) or not 0 <= word < 1 << 32:
            raise ValueError("invalid host write")
        if self.window != window:
            self.window = window
            self.cycle()
        for nibble in range(8):
            ui = window << 6 | 16 | (word >> (4 * nibble) & 15)
            for _ in range(timeout):
                if self.cycle(ui).uo & 16:
                    break
            else:
                raise TimeoutError("host write backpressure did not clear")
        self.cycle()

    def read(self, window: int = 0, *, timeout: int = 100000,
             pauses: Iterable[int] = ()) -> int:
        if window not in (0, 3):
            raise ValueError("invalid host read")
        if self.window != window:
            self.window = window
            self.cycle()
        delays = iter(pauses)
        result = 0
        for nibble in range(8):
            self.idle(next(delays, 0))
            for _ in range(timeout):
                sampled = self.cycle(window << 6 | 32)
                if sampled.uo & 32:
                    result |= (sampled.uo & 15) << (4 * nibble)
                    break
            else:
                raise TimeoutError("host read data did not arrive")
        return result

    def command(self, opcode: int, payload: int = 0) -> None:
        if not 0 <= opcode <= 255 or not 0 <= payload < 1 << 24:
            raise ValueError("invalid host command")
        self.write(0, opcode << 24 | payload)

    def load(self, engine: int, words: Iterable[int], *, ownership: int = 0,
             open_drain: int = 0) -> None:
        image = tuple(words)
        self.command(0, engine)
        self.command(1)
        self.command(3, ownership | open_drain << 8)
        for word in image:
            self.write(1, word)
        self.command(2, len(image))

    def status(self, selection: int = 0) -> int:
        self.command(8, selection)
        # A window transition discards a previously snapshotted status word.
        self.window = 1
        self.cycle()
        return self.read(0)


def render_testbench(cycles: Iterable[Cycle], *, top: str = "tt_um_protocol_processor",
                     observations: bool = False) -> str:
    """Render a self-checking RTL testbench; only public pins are compared."""
    if not top.isidentifier():
        raise ValueError("invalid Verilog module identifier")
    lines = ["module tb;", "reg clk=0, rst_n=0, ena=1;",
             "reg [7:0] ui_in=0, uio_in=0;",
             "wire [7:0] uo_out, uio_out, uio_oe;",
             f"{top} dut(.clk(clk),.rst_n(rst_n),.ena(ena),.ui_in(ui_in),"
             ".uo_out(uo_out),.uio_in(uio_in),.uio_out(uio_out),.uio_oe(uio_oe));",
             "initial begin"]
    if observations:
        lines.insert(-1, "integer observed;")
        lines.append('observed=$fopen("observed.csv","w"); if (!observed) $fatal(1);')
    for i, step in enumerate(cycles):
        out = step.expected
        lines += [f"clk=0; rst_n=1'b{int(not step.reset)}; ena=1'b{int(step.enabled)}; "
                  f"ui_in=8'h{step.ui:02x}; uio_in=8'h{step.pins:02x}; #5; clk=1; #1;",
                  f"if ((uo_out & 8'h{255 if out.uo & 32 else 240:02x}) !== 8'h{out.uo:02x} || uio_out !== 8'h{out.uio_out:02x} || "
                  f"uio_oe !== 8'h{out.uio_oe:02x}) begin "
                  f'$display("MISMATCH cycle={i} uo=%h out=%h oe=%h expected={out.uo:02x}/'
                  f'{out.uio_out:02x}/{out.uio_oe:02x}",uo_out,uio_out,uio_oe); $fatal(1); end #4;']
        if observations:
            lines.append(f'$fwrite(observed,"{i},%d,%d,%d,%d,%d\\n",ui_in,uio_in,uo_out,uio_out,uio_oe);')
    if observations:
        lines.append("$fclose(observed);")
    lines += ['$display("DIFFERENTIAL_OK"); $finish;', "end", "endmodule", ""]
    return "\n".join(lines)
