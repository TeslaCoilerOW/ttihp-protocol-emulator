# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""host/pio/asm.py (the rp2.asm_pio DSL for CPython), the lockstep program's
encoding and its clock/pin schedule (host/pio/pathcheck.py).

Optional oracles, run when available:
* PE_HOST_RP2_PY_DIR: a directory with MicroPython's ports/rp2/modules/rp2.py
  files named rp2_<tag>.py; each must assemble the lockstep program to the
  same program list as host/pio/asm.py.
* PE_HOST_PIOASM_DIR: a directory holding adafruit_pioasm.py (Adafruit's
  pioasm-syntax assembler); it must assemble the disassembly of the program to
  the same words.
"""

import glob
import importlib.util
import os
import sys
import types
import unittest

import support  # noqa: F401  (sys.path)
from pe_host.ports import pio_lockstep as pl
from pio import pathcheck
from pio.asm import PIOASMError, assemble, disassemble, execctrl_index

GOLDEN = [0x1009, 0x5024, 0xB042, 0x5004, 0x6264, 0x10E2, 0x1118, 0x6208, 0x121F, 0x108A,
          0xB0E0, 0x6024, 0x6061, 0x60A1, 0x6208, 0xB142, 0x10D8, 0x0092, 0x010F, 0x81A0,
          0x0016, 0x6208, 0x5158, 0x5008, 0xE000, 0x8040, 0xA025, 0x103D, 0x1198, 0x90A0,
          0x70A5, 0x6208]


def program():
    return assemble(pl.lockstep_program, **pl.asm_options(pathcheck._Pio))


class Encodings(unittest.TestCase):
    def test_instruction_words(self):
        """Encodings of the RP2040 datasheet instruction table (bits 15:13
        opcode, 12:8 delay/side-set, 7:0 operands)."""
        def prog():
            label("l")
            jmp("l")                    # 0x0000
            jmp(x_dec, "l")             # 0x0040
            jmp(not_osre, "l")          # 0x00E0
            wait(1, gpio, 5)            # 0x2085
            wait(0, pin, 2)             # 0x2022
            wait(1, irq, rel(3))        # 0x20D3
            in_(pins, 32)               # 0x4000
            in_(osr, 7)                 # 0x40E7
            out(pc, 5)                  # 0x60A5
            out(exec, 16)               # 0x60F0
            push(iffull, noblock)       # 0x8040
            push()                      # 0x8020
            pull(ifempty, block)        # 0x80E0
            pull(noblock)               # 0x8080
            mov(x, invert(y))           # 0xA02A
            mov(isr, reverse(osr))      # 0xA0D7
            mov(osr, status)            # 0xA0E5
            nop()                       # 0xA042
            irq(block, 2)               # 0xC022
            irq(clear, 7)               # 0xC047
            set(pindirs, 31)            # 0xE09F
            set(y, 0)                   # 0xE040
        prog_list, _ = assemble(prog)
        self.assertEqual([hex(w) for w in prog_list[0]],
                         [hex(w) for w in (0x0000, 0x0040, 0x00E0, 0x2085, 0x2022, 0x20D3,
                                           0x4000, 0x40E7, 0x60A5, 0x60F0, 0x8040, 0x8020,
                                           0x80E0, 0x8080, 0xA02A, 0xA0D7, 0xA0E5, 0xA042,
                                           0xC022, 0xC047, 0xE09F, 0xE040)])

    def test_side_set_and_delay_fields(self):
        """Side-set bits sit at the top of the 5-bit field (bit 12 is the enable
        bit when side-set is optional); the delay uses the rest."""
        def mandatory():
            nop()           .side(1)[15]
        def optional():
            nop()           .side(1)[7]
            nop()
        a, _ = assemble(mandatory, sideset_init=2)
        self.assertEqual(a[0][0], 0xA042 | 1 << 12 | 15 << 8)
        b, _ = assemble(optional, sideset_init=2)
        self.assertEqual(b[0][0], 0xA042 | 1 << 12 | 1 << 11 | 7 << 8)
        self.assertEqual(b[execctrl_index(b)] >> 30 & 1, 1)
        with self.assertRaises(PIOASMError):
            def too_long():
                nop()       .side(1)[16]
            assemble(too_long, sideset_init=2)

    def test_disassembler_round_trip(self):
        prog_list, _ = program()
        text = [disassemble(w, 1, False) for w in prog_list[0]]
        self.assertEqual(text[30], "out pc, 5 side 1")
        self.assertEqual(text[4], "out null, 4 side 0 [2]")


class LockstepProgram(unittest.TestCase):
    def test_golden_encoding_and_layout(self):
        prog_list, labels = program()
        self.assertEqual(list(prog_list[0]), GOLDEN)
        self.assertEqual(len(prog_list[0]), 32)
        execctrl = prog_list[execctrl_index(prog_list)]
        # wrap: fetch (24) .. the idle jump just before disp (28)
        self.assertEqual(((execctrl >> 7) & 31, (execctrl >> 12) & 31),
                         (labels["fetch"], labels["disp"] - 1))
        self.assertEqual(labels["rv_low"], 0)
        self.assertEqual(labels["rv_high"], 1)
        for name, addr in (("rd", pl.ADDR_RD), ("rdb", pl.ADDR_RDB), ("wr", pl.ADDR_WR),
                           ("halt", pl.ADDR_HALT), ("cyc", pl.ADDR_CYC),
                           ("fetch", pl.ADDR_FETCH)):
            self.assertEqual(labels[name], addr, name)
        self.assertEqual(pl.INSTR_JMP_FETCH, labels["fetch"])

    def test_every_path_keeps_the_frame(self):
        report = pathcheck.check_program()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["unreached"], [])
        self.assertEqual(report["drive_instructions"], [7, 14, 21, 24, 31])
        self.assertEqual(report["sample_instructions"], [3, 10, 16, 23])
        self.assertEqual(report["blocking"], [19, 29])

    def test_path_check_catches_a_broken_program(self):
        prog_list, labels = program()
        broken = list(prog_list)
        words = list(prog_list[0])
        words[24] = 0xE000 | 1 << 12             # set pins, 0 side 1 at slot 3
        broken[0] = words
        report = pathcheck.check_program(broken, labels)
        self.assertTrue(any("side 1 over slot 3" in e for e in report["errors"]), report["errors"])

    def test_build_program_sets_status_n(self):
        class FakeRp2:
            from pio.asm import asm_pio
            asm_pio = staticmethod(asm_pio)

            class PIO:
                OUT_LOW = 2
                SHIFT_RIGHT = 1
        prog_list = pl.build_program(FakeRp2)
        self.assertEqual(prog_list[execctrl_index(prog_list)] & 0x1F, pl.EXECCTRL_STATUS)


@unittest.skipUnless(os.environ.get("PE_HOST_RP2_PY_DIR"), "PE_HOST_RP2_PY_DIR not set")
class MicroPythonAssemblerOracle(unittest.TestCase):
    def test_real_rp2_asm_pio_matches(self):
        files = sorted(glob.glob(os.path.join(os.environ["PE_HOST_RP2_PY_DIR"], "rp2_*.py")))
        self.assertTrue(files)
        mine, _ = program()

        class PIO:
            SHIFT_LEFT, SHIFT_RIGHT = 0, 1
            JOIN_NONE, JOIN_TX, JOIN_RX = 0, 1, 2
            OUT_LOW, OUT_HIGH, IN_LOW, IN_HIGH = 2, 3, 0, 1
        stub = types.ModuleType("_rp2")
        stub.PIO = PIO
        const = types.ModuleType("micropython")
        const.const = lambda value: value
        saved = {name: sys.modules.get(name) for name in ("_rp2", "micropython")}
        sys.modules["_rp2"], sys.modules["micropython"] = stub, const
        try:
            for path in files:
                spec = importlib.util.spec_from_file_location("rp2_oracle", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                real = module.asm_pio(**pl.asm_options(PIO))(pl.lockstep_program)
                self.assertEqual(list(real[0]), list(mine[0]), path)
                self.assertEqual(list(real[len(real) - 5:]), list(mine[len(mine) - 5:]), path)
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module


@unittest.skipUnless(os.environ.get("PE_HOST_PIOASM_DIR"), "PE_HOST_PIOASM_DIR not set")
class PioasmOracle(unittest.TestCase):
    def test_adafruit_pioasm_matches(self):
        sys.path.insert(0, os.environ["PE_HOST_PIOASM_DIR"])
        import adafruit_pioasm
        mine, _ = program()
        text = [".side_set 1"] + [disassemble(w, 1, False) for w in mine[0]]
        self.assertEqual(list(adafruit_pioasm.assemble("\n".join(text))), list(mine[0]))


if __name__ == "__main__":
    unittest.main()
