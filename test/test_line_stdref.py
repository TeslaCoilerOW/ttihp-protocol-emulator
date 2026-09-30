# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Line unit at the pads against the standards-based reference (diet8_rec16).

The oracle is test/model/line_std_ref.py, written from docs/isa.md and the
public standards (USB 2.0, CAN 2.0, IEEE 802.3, the reveng CRC catalogue);
no check here uses the lockstep model (test/model/line_unit.py) or the
harness that runs it.  The driver is test/stdref/chip.py; programs and the
stimuli come from test/stdref/cases.py and demos.py; the cycle schedule of
each engine is test/stdref/program.py.

For every engine the module compares uio_out and uio_oe of the engine's own
pins in every cycle from START to the end of the program with the
reference's prediction, decodes every driven line XFER from the pads with
``decode_pad_trace``, and compares the pushed words (CRC, LSTAT, rx, TIME
differences), the FIFO levels and the fault status.

Environment:
  PE_STDREF_SEED     base seed (default 0x57D2EF)
  PE_STDREF_CASES    random cases (default 2000 RTL, 4 gate level)
  PE_STDREF_FIRST    first case index (default 0)
  PE_STDREF_VERBOSE  log the mismatches of every rejected reading (open points)
  PE_STDREF_MAX_FAILURES  stop the random test after this many failing cases (default 5)
Case k uses seed (base + k).  At gate level (PE_GATE_LEVEL=1), as in
test_line_demos, the CAN demo is skipped, the USB demo runs one data set and
the 10BASE-T demo stops after the frame, unless PE_LINE_GL_FULL=1.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import random
import sys

import cocotb

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "stdref"))

from _ref import ref  # noqa: E402
import cases as gen  # noqa: E402
import demos  # noqa: E402
from chip import CLEAR, Chip, SELECT, STOP  # noqa: E402
from program import ProgramRef, encode  # noqa: E402

GATE_LEVEL = bool(os.environ.get("PE_GATE_LEVEL"))
SHORT = GATE_LEVEL and not os.environ.get("PE_LINE_GL_FULL")
VERBOSE = bool(os.environ.get("PE_STDREF_VERBOSE"))
VARIANT = os.environ.get("PE_VARIANT", "diet8_rec16")
SKIP = VARIANT != "diet8_rec16"

# The readings of the points docs/isa.md leaves open that the RTL follows
# (test_contract_open_points measures them; test/stdref/README.md lists them).
RTL_READING = ref.Interpretation(
    lstat_level_from_samples=False,
    drive_only_sets_prev_sample=False,
    stuff_error_state="reset",
    se0_needs_pair=True,
    pair_rule_without_drive=False,
    either_len1_invalid_without_stuffing=False,
    classic_ds_crc_basis="received",
    lcfg_cancels_pending_half=True,
    ltim_pending_half="next_mid",
)


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "")
    return int(value, 0) if value else default


# ---------------------------------------------------------------------------
# running one set of engine programs
# ---------------------------------------------------------------------------

class Run:
    """Predict, run and compare one case (one to four engines)."""

    def __init__(self, case: gen.Case, interp: ref.Interpretation = RTL_READING):
        self.case = case
        self.interp = interp
        self.transmitters = {e.engine: gen.Transmitter(e, interp) for e in case.engines}
        self.programs: dict[int, ProgramRef] = {}
        self.outcomes = {}
        self.start = None
        for e in case.engines:
            self.programs[e.engine] = self._predictor(e)
            self.outcomes[e.engine] = self.programs[e.engine].run(0)

    def _predictor(self, e: gen.EngineCase) -> ProgramRef:
        tr = self.transmitters[e.engine]
        d, q = e.data_pin, e.pair_pin
        holder = {}

        def pad_in(r: int) -> int:
            prog = holder["prog"]
            if e.scenario == "rx":
                rx, pair = tr.sample_request(r)
                return rx << d | pair << q
            out, oe = prog.output_at(r), prog.oe_at(r)
            od = (out >> d) & 1 if (oe >> d) & 1 else 1
            oq = (out >> q) & 1 if (oe >> q) & 1 else 1
            return (od & tr.rival_level(r)) << d | oq << q

        prog = ProgramRef(e.words, owned=e.owned, tx_words=list(e.tx_words), interp=self.interp,
                          pad_in=pad_in, hooks=tr, max_cycles=400_000)
        holder["prog"] = prog
        return prog

    def pad_fn(self, n: int, out: int, oe: int) -> int:
        r = n - self.start if self.start is not None else -(1 << 40)
        out = max(out, 0)
        oe = max(oe, 0)
        value = 0
        for e in self.case.engines:
            tr = self.transmitters[e.engine]
            d, q = e.data_pin, e.pair_pin
            if e.scenario == "rx":
                rx, pair = tr.played(r)
                value |= rx << d | pair << q
            else:
                od = (out >> d) & 1 if (oe >> d) & 1 else 1
                oq = (out >> q) & 1 if (oe >> q) & 1 else 1
                value |= (od & tr.rival_level(r)) << d | oq << q
        return value

    @property
    def end(self) -> int:
        return max(o.end_cycle for o in self.outcomes.values())

    async def execute(self, chip: Chip) -> None:
        mask = 0
        for e in self.case.engines:
            await chip.load(e.engine, e.words, owned=e.owned)
            if e.tx_words:
                written = await chip.fill_tx(e.engine, e.tx_words)
                assert written == len(e.tx_words), "TX prefill"
            mask |= 1 << e.engine
        chip.pad_fn = self.pad_fn
        chip.recording = True
        chip.out.clear()
        chip.oe.clear()
        self.start = None
        self.start = await chip.start(mask)
        while chip.n <= self.start + self.end + 4:
            await chip.cycle()
        chip.recording = False

    async def collect(self, chip: Chip) -> dict:
        got = {}
        for e in self.case.engines:
            await chip.command(SELECT, e.engine)
            status = await chip.status(0)
            levels = await chip.status(2)
            words = []
            for _ in range((levels >> 16) & 0xFFFF):
                words.append(await chip.read(3))
            got[e.engine] = (status, levels, words)
        await chip.command(STOP, 0xF)
        await chip.command(CLEAR, 0xF)
        chip.pad_fn = None
        return got

    def compare(self, chip: Chip, got: dict) -> list[str]:
        problems: list[str] = []
        time_offsets = set()
        for e in self.case.engines:
            prog, out = self.programs[e.engine], self.outcomes[e.engine]
            label = f"engine {e.engine} ({e.scenario})"
            mask = e.owned
            # pads, every cycle
            dont_care = self._dont_care(prog)
            for r in range(1, out.end_cycle + 1):
                n = self.start + r
                want_out, want_oe = prog.output_at(r) & mask, prog.oe_at(r) & mask
                have_out, have_oe = chip.out.get(n, -1), chip.oe.get(n, -1)
                if have_out < 0 or have_oe < 0:
                    problems.append(f"{label}: unresolved pads in cycle {r}")
                    break
                skip = 0
                for lo, hi, m in dont_care:
                    if lo <= r < hi:
                        skip |= m
                if ((have_out & mask) ^ want_out) & ~skip or (have_oe & mask) != want_oe:
                    problems.append(
                        f"{label}: cycle {r} (pc {self._pc_at(out, r)}): uio_out {have_out & mask:02x} "
                        f"want {want_out:02x}, uio_oe {have_oe & mask:02x} want {want_oe:02x}")
                    break
            # output enables released after HALT or a fault
            n = self.start + out.end_cycle + 1
            if chip.oe.get(n, -1) & mask:
                problems.append(f"{label}: output enables not released after cycle {out.end_cycle}")
            # every driven line XFER decoded from the pads
            problems += self._decode_drives(chip, e, prog, out, label)
            # status, levels, pushed words
            status, levels, words = got[e.engine]
            fault_bit = (status >> 3) & 1
            code = (status >> 8) & 0xFF
            if out.fault > 0:
                if not fault_bit or code != out.fault:
                    problems.append(f"{label}: status {status:#x}, want fault {out.fault}")
            elif fault_bit or status & 1:
                problems.append(f"{label}: status {status:#x}, want halted without fault")
            want_words = [w for _, w in out.pushes]
            if len(words) != len(want_words):
                problems.append(f"{label}: {len(words)} words pushed, want {len(want_words)}")
            for i, (have, want) in enumerate(zip(words, want_words)):
                if out.push_is_time[i]:
                    time_offsets.add((have - want) & 0xFFFFFFFF)
                elif have != want:
                    problems.append(f"{label}: push {i} = {have:#010x}, want {want:#010x}")
            if (levels & 0xFFFF) != 0:
                problems.append(f"{label}: TX level {levels & 0xFFFF} after the program, want 0")
        if len(time_offsets) > 1:
            problems.append(f"TIME values are not one offset from the predicted issue cycles: {time_offsets}")
        return problems

    @staticmethod
    def _pc_at(out, r: int) -> int:
        pc = 0
        for p, cycle in out.issue_cycles:
            if cycle <= r:
                pc = p
        return pc

    @staticmethod
    def _dont_care(prog: ProgramRef) -> list[tuple[int, int, int]]:
        windows = []
        for lo, _, m in prog.dont_care:
            later = [w.cycle + ref.OUT_LATENCY for w in prog.writes
                     if w.mask & m and w.cycle + ref.OUT_LATENCY > lo and w.source != "XFER-data"]
            windows.append((lo, min(later) if later else 1 << 60, m))
        return windows

    def _decode_drives(self, chip: Chip, e: gen.EngineCase, prog: ProgramRef, out, label) -> list[str]:
        problems = []
        unit_cfgs = self._configs(prog, out)
        for index, xfer in enumerate(out.xfers):
            if not xfer.cells or xfer.cells[0].boundary is None or not xfer.data_sent:
                continue
            ltim_cycle, ltim_imm, lcfg_imm, pins = unit_cfgs[index]
            cfg = ref.Lcfg.decode(lcfg_imm)
            first = xfer.cells[0].boundary
            ticker = ref.Ticker(ltim_cycle, ref.Ltim.decode(ltim_imm))
            k0 = ticker.first_after(first - 1, 0)
            last = xfer.cells[-1].boundary
            end = ticker.tick(ticker.first_after(last, 0)) + ref.OUT_LATENCY
            # the last cell lasts until the next boundary tick unless another
            # instruction or XFER writes one of its pins earlier
            own = {(w.cycle, w.pin) for w in xfer.writes}
            pin_mask = (1 << pins.tx) | (1 << pins.clock if cfg.pair else 0)
            later = [w.cycle + ref.OUT_LATENCY for w in prog.writes
                     if w.cycle > last and w.mask & pin_mask
                     and not any((w.cycle, p) in own for p in range(8) if (w.mask >> p) & 1)]
            stop = min([end, out.end_cycle + 1] + later)
            if cfg.code == ref.MANCHESTER:
                mid = xfer.cells[-1].mid
                if not any(w.source == "unit-mid" and w.cycle == mid and w.mask >> pins.tx & 1
                           for w in prog.writes):
                    # LCFG or LTIM dropped or moved the second half: only the first is fixed
                    stop = min(stop, mid + ref.OUT_LATENCY)
            data = [(chip.out.get(self.start + r, 0) >> pins.tx) & 1 for r in range(stop + 1)]
            pair = [(chip.out.get(self.start + r, 0) >> pins.clock) & 1 for r in range(stop + 1)]
            head = xfer.cells[0]
            # NRZI: the unit's line level before the first cell (LCFG sets it, not the pin)
            level_before = head.level if head.line_bit else head.level ^ 1
            decoded = ref.decode_pad_trace(
                data, ltim_cycle, ltim_imm, lcfg_imm, k0, pair_trace=pair if cfg.pair else None,
                cells=len(xfer.cells), level=level_before, check_until=stop,
                run_state=ref.RunState())
            want_line = [c.line_bit for c in xfer.cells]
            if decoded.line_bits != want_line or decoded.violations:
                problems.append(f"{label}: XFER {index} decoded line bits {decoded.line_bits} "
                                f"want {want_line}; {decoded.violations[:3]}")
        return problems

    @staticmethod
    def _configs(prog: ProgramRef, out) -> list[tuple[int, int, int, ref.Pins]]:
        """(LTIM cycle, LTIM imm, LCFG imm, PINS) in force for each line XFER."""
        result = []
        ltim = (0, 0)
        lcfg = 0
        pins = ref.Pins(0, 0, 0)
        for pc, cycle in out.issue_cycles:
            word = prog.words[pc]
            op = word >> 24
            if op == ref.LTIM_OP:
                ltim = (cycle, word & 0xFFFFFF)
            elif op == ref.LCFG_OP:
                lcfg = word & 0xFFFFFF
            elif op == 16:
                pins = ref.Pins.decode(word & 0xFFFFFF)
            elif op == ref.XFER_OP and (word >> 5) & 1:
                result.append((ltim[0], ltim[1], lcfg, pins))
        return result[:len(out.xfers)]


async def run_case(chip: Chip, case: gen.Case, interp: ref.Interpretation = None) -> list[str]:
    run = Run(case, interp or RTL_READING)
    await chip.reset(2)
    await run.execute(chip)
    got = await run.collect(chip)
    return run.compare(chip, got)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@cocotb.test(skip=SKIP)
async def test_base_timing_convention(dut):
    """SET is on the pad one cycle after issue; WAITPIN sees a pad two cycles later; START issues pc 0 on the next edge."""
    chip = Chip(dut)
    await chip.begin()
    words = [encode("SET", imm=1), encode("WAITPIN", 1, 1), encode("SET", imm=0), encode("HALT")]
    await chip.load(0, words, owned=1)
    rise_at = {}

    def pads(n, out, oe):
        return 2 if "y" in rise_at and n >= rise_at["y"] else 0

    chip.pad_fn = pads
    chip.recording = True
    s = await chip.start(1)
    rise_at["y"] = s + 20
    await chip.idle(40)
    ones = [n for n in sorted(chip.out) if n >= s and chip.out[n] & 1]
    assert ones, "SET 1 never reached the pad"
    x = ones[0]
    z = ones[-1] + 1
    assert x == s + ref.OUT_LATENCY, f"SET issued in cycle {s} is on the pad in cycle {x}"
    assert z == rise_at["y"] + ref.IN_LATENCY + 1 + ref.OUT_LATENCY, \
        f"pad raised in cycle {rise_at['y']}, WAITPIN+SET visible in cycle {z}"


@cocotb.test(skip=SKIP)
async def test_discovery_capability_word(dut):
    """READ_SELECT 7 equals the capability word the contract defines (0x000F5F03)."""
    chip = Chip(dut)
    await chip.begin()
    word = await chip.status(7)
    assert word == ref.capability_word(version=3, engines=0xF), f"{word:#010x}"


def _pads_equal(chip: Chip, start: int, prog: ProgramRef, mask: int, until: int,
                out=None) -> list[str]:
    """Pads of ``mask`` equal to the prediction in cycles 1..until-1 (to the end of the program)."""
    problems = []
    if out is not None and out.fault >= 0:
        until = min(until, out.end_cycle + 1)
        if chip.oe.get(start + out.end_cycle + 1, -1) & mask:
            problems.append(f"output enables not released after cycle {out.end_cycle}")
    for r in range(1, until):
        n = start + r
        want_out, want_oe = prog.output_at(r) & mask, prog.oe_at(r) & mask
        have_out, have_oe = chip.out.get(n, -1) & mask, chip.oe.get(n, -1) & mask
        if have_out != want_out or have_oe != want_oe:
            problems.append(f"cycle {r}: uio_out {have_out:02x} want {want_out:02x}, "
                            f"uio_oe {have_oe:02x} want {want_oe:02x}")
            break
    return problems


@cocotb.test(skip=SKIP)
async def test_demo_crc16_stream(dut):
    """crc16-stream: pushed CRCs equal CRC-16/IBM-3740 and XMODEM (reveng catalogue); pin 0 carries the words MSB first."""
    chip = Chip(dut)
    await chip.begin()
    img = demos.load_image("crc16-stream")
    rng = random.Random(16)
    for init in (0xFFFF, 0x0000):
        await chip.reset(2)
        data_words = [rng.randrange(1 << 32) for _ in range(3)]
        tx, expected = demos.crc16_stream_job(init, data_words)
        prog = ProgramRef(img.words, owned=img.owned, tx_words=list(tx), interp=RTL_READING)
        out = prog.run(0)
        assert [w for _, w in out.pushes] == [expected]
        await chip.load(img.engine, img.words, owned=img.owned)
        assert await chip.fill_tx(img.engine, tx) == len(tx)
        chip.recording = True
        chip.out.clear()
        s = await chip.start(1)
        await chip.idle(out.end_cycle + 10)
        chip.recording = False
        problems = _pads_equal(chip, s, prog, img.owned, out.end_cycle, out)
        assert not problems, problems
        await chip.command(SELECT, img.engine)
        got = await chip.read(3)
        assert got == expected, f"init {init:#x}: CRC {got:#06x}, want {expected:#06x}"
        ltim_cycle = [c for p, c in out.issue_cycles if p == 1][0]
        ticker = ref.Ticker(ltim_cycle, ref.Ltim.decode(1))
        trace = [chip.out.get(s + r, 0) & 1 for r in range(out.end_cycle + 2)]
        for word, xfer in zip(data_words, out.xfers):
            k0 = ticker.first_after(xfer.cells[0].boundary - 1, 0)
            decoded = ref.decode_pad_trace(trace, ltim_cycle, 1, 0, k0, count=32, msb_first=True,
                                           check_until=xfer.complete + 2)
            assert decoded.data == ref.word_to_bits(word, 32, True) and not decoded.violations
        await chip.command(STOP, 1)


@cocotb.test(skip=SKIP)
async def test_demo_10base_t_udp(dut):
    """10base-t-udp: a 68-byte UDP frame decoded from TD+/TD- (IEEE 802.3 Manchester), preamble/SFD, CRC-32 residue, link pulse."""
    chip = Chip(dut)
    await chip.begin()
    img = demos.load_image("10base-t-udp")
    frame = demos.udp_frame()
    words = demos.frame_words_lsb_first(frame)
    horizon = 3200
    prog = ProgramRef(img.words, owned=img.owned, tx_words=list(words), stop_cycle=horizon,
                      interp=RTL_READING)
    out = prog.run(0)
    await chip.load(img.engine, img.words, owned=img.owned)
    assert await chip.fill_tx(img.engine, words[:8]) == 8
    chip.recording = True
    s = await chip.start(1)
    pending = list(words[8:])
    # top the TX queue up while the frame goes out (the host does nothing else)
    while pending:
        if await chip.try_write(2, pending[0], max_wait=1):
            pending.pop(0)
    while chip.n < s + horizon:
        await chip.cycle()
    chip.recording = False
    ltim_cycle = [c for p, c in out.issue_cycles if p == 2][0]
    first = out.xfers[0].cells[0].boundary
    last = out.xfers[-1].cells[-1].boundary
    problems = _pads_equal(chip, s, prog, img.owned, horizon - 10, out)
    assert not problems, problems
    ticker = ref.Ticker(ltim_cycle, ref.Ltim.decode(2))
    k0 = ticker.first_after(first - 1, 0)
    tdp = [(chip.out.get(s + r, 0) >> 0) & 1 for r in range(horizon)]
    tdm = [(chip.out.get(s + r, 0) >> 1) & 1 for r in range(horizon)]
    decoded = ref.decode_pad_trace(tdp, ltim_cycle, 2, 130, k0, pair_trace=tdm, count=608,
                                   check_until=last + 1 + 4)
    assert not decoded.violations, decoded.violations[:4]
    assert decoded.data == ref.ethernet_line_bits(frame)
    assert ref.CRC32_ETHERNET.residue_of(decoded.data[64:]) == ref.CRC32_ETHERNET.residue
    await chip.command(STOP, 1)
    if not SHORT:
        # an idle transmitter sends a 4-cycle (100 ns at 40 MHz) link pulse
        await chip.reset(2)
        prog = ProgramRef(img.words, owned=img.owned, tx_words=[], stop_cycle=300, interp=RTL_READING)
        out = prog.run(0)
        await chip.load(img.engine, img.words, owned=img.owned)
        chip.recording = True
        chip.out.clear()
        chip.oe.clear()
        s = await chip.start(1)
        await chip.idle(300)
        problems = _pads_equal(chip, s, prog, img.owned, 290, out)
        assert not problems, problems
        await chip.command(STOP, 1)


def _usb_session(chip: Chip, img, data: bytes, token_start: int, length: int):
    dplus, dminus = demos.usb_host_packet(token_start, demos.usb_in_token(0x70, 4), length)
    holder = {}

    def pad_in(r):
        prog = holder["prog"]
        oe, own = prog.oe_at(r), prog.output_at(r)
        value = 0
        for pin, host in ((0, dplus), (1, dminus)):
            level = (own >> pin) & 1 if (oe >> pin) & 1 else (host[r] if 0 <= r < length else host[-1])
            value |= level << pin
        return value

    prog = ProgramRef(img.words, owned=img.owned, tx_words=demos.usb_data1_words(data),
                      pad_in=pad_in, stop_cycle=length - 50, interp=RTL_READING)
    holder["prog"] = prog
    out = prog.run(0)

    def rtl_pads(n, o, e):
        s = chip_start.get("s")
        r = n - s if s is not None else -1
        value = 0
        for pin, host in ((0, dplus), (1, dminus)):
            if (max(e, 0) >> pin) & 1:
                level = (max(o, 0) >> pin) & 1
            else:
                level = host[r] if 0 <= r < length else host[0 if r < 0 else -1]
            value |= level << pin
        return value

    chip_start = {}
    return prog, out, rtl_pads, chip_start


@cocotb.test(skip=SKIP)
async def test_demo_usb_ls_in_responder(dut):
    """usb-ls-in-responder: IN token (USB-IF CRC5, NRZI, stuffing) in; DATA1 + CRC16 out decoded from D+/D-, EOP SE0 then J."""
    chip = Chip(dut)
    await chip.begin()
    img = demos.load_image("usb-ls-in-responder")
    for data in (bytes(range(0x10, 0x18)), b"\xff" * 8)[:1 if SHORT else 2]:
        await chip.reset(2)
        length = 9000
        prog, out, rtl_pads, box = _usb_session(chip, img, data, 300, length)
        await chip.load(img.engine, img.words, owned=img.owned)
        assert await chip.fill_tx(img.engine, demos.usb_data1_words(data)) == 3
        chip.pad_fn = rtl_pads
        chip.recording = True
        chip.out.clear()
        chip.oe.clear()
        box["s"] = None
        s = await chip.start(1)
        box["s"] = s
        await chip.idle(length - 60 - (chip.n - s))
        chip.recording = False
        chip.pad_fn = None
        problems = _pads_equal(chip, s, prog, img.owned, length - 60, out)
        assert not problems, problems
        response = out.xfers[4:8]
        assert out.xfers[3].se0 and out.xfers[2].data_received == ref.usb_token_body(0x70, 4)
        ltim_cycle = [c for p, c in out.issue_cycles if p == 27][0]
        ticker = ref.Ticker(ltim_cycle, ref.Ltim.decode(6269712))
        k0 = ticker.first_after(response[0].cells[0].boundary - 1, 0)
        last = response[-1].cells[-1].boundary
        dp = [(chip.out.get(s + r, 0) >> 0) & 1 for r in range(length - 60)]
        dm = [(chip.out.get(s + r, 0) >> 1) & 1 for r in range(length - 60)]
        expected = ref.usb_packet_bits("DATA1", ref.usb_data_body(data))
        decoded = ref.decode_pad_trace(dp, ltim_cycle, 6269712, 213, k0, pair_trace=dm,
                                       count=len(expected), level=0, check_until=last + 1 + 33)
        assert not decoded.violations, decoded.violations[:4]
        assert decoded.data == expected
        assert ref.CRC16_USB.residue_of(decoded.data[16:]) == ref.CRC16_USB.residue
        se0 = [r for r in range(last + 1, length - 60) if dp[r] == 0 and dm[r] == 0]
        assert se0 and se0[-1] - se0[0] + 1 == len(se0)
        assert dp[se0[-1] + 1] == 0 and dm[se0[-1] + 1] == 1
        await chip.command(STOP, 1)


async def _can_session(chip: Chip, nodes, tx_words, length):
    img = demos.load_image("can-node")
    holder = {}

    def device_level(r):
        prog = holder["prog"]
        return (prog.output_at(r) & 1) if prog.oe_at(r) & 1 else 1

    bus = demos.Bus(device_level, nodes["model"])

    def pad_in(r):
        prog = holder["prog"]
        return (prog.output_at(r) & 1) | (bus.level(r) << 1) if r >= 0 else 2

    prog = ProgramRef(img.words, owned=img.owned, tx_words=list(tx_words), pad_in=pad_in,
                      stop_cycle=length, interp=RTL_READING)
    holder["prog"] = prog
    out = prog.run(0)
    bus.level(length)
    # the RTL side: fresh node objects, the bus from the RTL's TXD
    rtl_nodes = nodes["rtl"]
    rtl_levels = {}
    box = {"s": None}

    def rtl_pads(n, o, e):
        s = box["s"]
        if s is None:
            return 2 | (max(o, 0) & 1)
        r = n - s
        level = (max(o, 0) & 1) if (max(e, 0) & 1) else 1
        for node in rtl_nodes:
            level &= node.drive(r)
        for node in rtl_nodes:
            node.observe(r, level)
        rtl_levels[r] = level
        return (max(o, 0) & 1) | level << 1

    await chip.reset(2)
    await chip.load(img.engine, img.words, owned=img.owned)
    if tx_words:
        assert await chip.fill_tx(img.engine, list(tx_words)) == len(tx_words)
    chip.pad_fn = rtl_pads
    chip.recording = True
    chip.out.clear()
    chip.oe.clear()
    s = await chip.start(1)
    box["s"] = s
    while chip.n < s + length:
        await chip.cycle()
    chip.recording = False
    chip.pad_fn = None
    problems = _pads_equal(chip, s, prog, img.owned, length - 5, out)
    await chip.command(SELECT, img.engine)
    status = await chip.status(0)
    levels = await chip.status(2)
    pushed = [await chip.read(3) for _ in range((levels >> 16) & 0xFFFF)]
    await chip.command(STOP, 1)
    await chip.command(CLEAR, 1)
    return prog, out, problems, status, pushed, bus, rtl_levels


@cocotb.test(skip=SKIP or SHORT)
async def test_demo_can_node(dut):
    """can-node against CAN 2.0A peers: a frame sent and ACKed by a receiver, a frame received and ACKed, a lost arbitration (fault 72)."""
    chip = Chip(dut)
    await chip.begin()
    data = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x00, 0x1F, 0x80])
    # 1. transmit
    nodes = {"model": [demos.CanReceiverNode(100)], "rtl": [demos.CanReceiverNode(100)]}
    prog, out, problems, status, pushed, bus, levels = await _can_session(
        chip, nodes, demos.can_tx_words(0x123, data), 16000)
    if problems:
        dut._log.error("status %#x; bus 150..2100: %s", status,
                       "".join(str(levels.get(r, "-")) for r in range(150, 2100, 25)))
    assert not problems, (problems, f"status {status:#x}")
    frame = nodes["rtl"][0].frames[0]
    assert frame["crc_ok"] and frame["ident"] == 0x123 and frame["data"] == data
    assert not status & 8, f"status {status:#x}"
    # 2. receive
    rx_data = bytes([1, 2, 3, 4, 0xF0, 0x0F, 0xFF, 0x00])
    nodes = {"model": [demos.CanTransmitterNode(0x2A5, rx_data, 100, start=600)],
             "rtl": [demos.CanTransmitterNode(0x2A5, rx_data, 100, start=600)]}
    prog, out, problems, status, pushed, bus, _ = await _can_session(chip, nodes, [], 16000)
    assert not problems, problems
    assert nodes["rtl"][0].ack_seen is True, "no ACK in the ACK slot"
    fields = ref.can_frame(0x2A5, rx_data)["fields"]
    assert len(pushed) == 3
    assert pushed[0] & ((1 << 19) - 1) == ref.bits_to_int(fields[:19], lsb_first=False)
    assert pushed[1:] == [int.from_bytes(rx_data[:4], "big"), int.from_bytes(rx_data[4:], "big")]
    # 3. arbitration lost to a simultaneous SOF with a lower identifier
    nodes = {"model": [demos.CanTransmitterNode(0x122, bytes([0x55] * 8), 100, sync_on_sof=True)],
             "rtl": [demos.CanTransmitterNode(0x122, bytes([0x55] * 8), 100, sync_on_sof=True)]}
    prog, out, problems, status, pushed, bus, _ = await _can_session(
        chip, nodes, demos.can_tx_words(0x123, bytes(8)), 6000)
    assert out.fault == 72
    assert not problems, problems
    assert status & 8 and (status >> 8) & 0xFF == 72, f"status {status:#x}"


def _trailing_stuff_se0_engines():
    """Sample XFERs whose last data bit makes a trailing stuff bit due; the line
    shows SE0 in that stuff cell instead."""
    I, M = gen.ins, gen.imm
    out = []
    for label, cfg, bits in (
            ("runs of 1s, run length 3", ref.Lcfg(ref.NRZ, True, False, 3, True, False, True, 1), [1, 1, 1]),
            ("either polarity, run length 2", ref.Lcfg(ref.NRZ, True, True, 2, True, False, True, 1), [0, 0])):
        words = [M("PINS", ref.Pins(1, 0, 0).encode()), M("DIR", 0), M("LCFG", cfg.encode()),
                 M("LTIM", 4), I("XFER", len(bits), 0, gen.LINE | gen.SAMPLE), I("LSTAT", 1),
                 I("PUSH"), I("HALT")]
        out.append((label, _eng("rx", words, rx_plans=[gen.RxPlan(bits, se0_at=len(bits),
                                                                   trailing=False)])))
    return out


@cocotb.test(skip=SKIP)
async def test_se0_in_trailing_stuff_cell_leaves_no_data_bits(dut):
    """SE0 in the cell of a due trailing stuff bit ends the XFER; LSTAT reports SE0 and 0 data bits remaining (isa.md LSTAT [13:8])."""
    chip = Chip(dut)
    await chip.begin()
    failures = []
    for label, engine in _trailing_stuff_se0_engines():
        problems = await run_case(chip, gen.Case(0, [engine]), RTL_READING)
        if problems:
            other = dataclasses.replace(RTL_READING, se0_left_counts_trailing_stuff=True)
            again = await run_case(chip, gen.Case(0, [engine]), other)
            dut._log.error("%s: %s; with the trailing stuff cell counted as 1 bit: %s",
                           label, problems[:3], again[:3] or "matches")
            failures.append((label, problems[:3]))
    assert not failures, failures


@cocotb.test(skip=SKIP)
async def test_random_line_programs(dut):
    """Seeded random line programs on one to four engines: pads every cycle, decoded XFERs, pushed words and status against line_std_ref."""
    chip = Chip(dut)
    await chip.begin()
    seed = env_int("PE_STDREF_SEED", 0x57D2EF)
    count = env_int("PE_STDREF_CASES", 4 if GATE_LEVEL else 2000)
    first = env_int("PE_STDREF_FIRST", 0)
    failures = []
    max_failures = env_int("PE_STDREF_MAX_FAILURES", 5)
    not_run = []
    explained: dict[str, int] = {}
    stats = {"engines": 0, "xfers": 0, "cycles": 0}
    features: dict[str, int] = {}
    last = first - 1
    for k in range(first, first + count):
        last = k
        case = gen.make_case(seed + k)
        try:
            run = Run(case, RTL_READING)
        except ref.XferDoesNotEnd as error:
            # the reference predicts a sampling XFER that never ends: nothing to compare
            not_run.append(k)
            dut._log.warning("case %d (seed %#x) not run: %s", k, seed + k, error)
            continue
        await chip.reset(2)
        start_n = chip.n
        await run.execute(chip)
        got = await run.collect(chip)
        problems = run.compare(chip, got)
        stats["engines"] += len(case.engines)
        stats["xfers"] += sum(len(o.xfers) for o in run.outcomes.values())
        stats["cycles"] += chip.n - start_n
        _count_features(features, case, run)
        if problems:
            failures.append((k, problems[:4]))
            dut._log.error("case %d (seed %#x): %s", k, seed + k, problems[:4])
            for reading in _explained_by(case, run, chip, got):
                explained[reading] = explained.get(reading, 0) + 1
                dut._log.error("case %d: the RTL matches line_std_ref with %s", k, reading)
            if len(failures) >= max_failures:
                break
    dut._log.info("stdref random: seed %#x, cases %d..%d, %d engine programs, %d line XFERs, "
                  "%d cycles, %d failures, %d not run %s",
                  seed, first, last, stats["engines"], stats["xfers"], stats["cycles"],
                  len(failures), len(not_run), not_run[:20])
    dut._log.info("stdref random features: %s",
                  ", ".join(f"{k} {v}" for k, v in sorted(features.items())))
    if failures:
        dut._log.info("stdref random failures explained by a named reading: %s (of %d)",
                      explained or "none", len(failures))
    assert not failures, failures
    assert len(not_run) * 100 <= count, f"too many cases not run: {not_run}"


# Readings that differ from the contract's text or from RTL_READING, tried on a
# failing case to name the difference (diagnosis only: the case still fails).
DIAGNOSTIC_READINGS = {
    "se0_left_counts_trailing_stuff=True": {"se0_left_counts_trailing_stuff": True},
}


def _explained_by(case: gen.Case, run: Run, chip: Chip, got: dict) -> list[str]:
    names = []
    for name, fields in DIAGNOSTIC_READINGS.items():
        try:
            other = Run(case, dataclasses.replace(run.interp, **fields))
        except ref.XferDoesNotEnd:
            continue
        other.start = run.start
        if not other.compare(chip, got):
            names.append(name)
    return names


def _count_features(features: dict, case: gen.Case, run: Run) -> None:
    """Tally what the checked line XFERs exercised (logged at the end)."""
    def add(key, n=1):
        features[key] = features.get(key, 0) + n

    for e in case.engines:
        out = run.outcomes[e.engine]
        add("scenario " + e.scenario)
        if e.strobe:
            add("rx strobe engines")
        if out.fault > 0:
            add(f"fault {out.fault}")
        configs = Run._configs(run.programs[e.engine], out)
        for xfer, (_, ltim_imm, lcfg_imm, _) in zip(out.xfers, configs):
            cfg, ltim = ref.Lcfg.decode(lcfg_imm), ref.Ltim.decode(ltim_imm)
            code = ("NRZ", "NRZI", "Manchester")[cfg.code]
            drive = xfer.cells and xfer.cells[0].boundary is not None
            sample = xfer.cells and xfer.cells[-1].sample is not None
            add(f"{code} {'drive+sample' if drive and sample else 'drive' if drive else 'sample'}")
            if cfg.stuffing:
                add("stuffing " + ("either" if cfg.either else "1s") + f" len {cfg.run_length}")
                add("stuff cells", sum(c.stuff for c in xfer.cells))
            if cfg.pair:
                add("pair")
            if ltim.fraction:
                add("fractional ticker")
            if xfer.se0:
                add("SE0 ends")
            if any(c.stuff_error for c in xfer.cells):
                add("stuff errors")
            if xfer.lost_at is not None:
                add("arbitration lost")
            if xfer.crc_fed:
                add("CRC fed")


# ---------------------------------------------------------------------------
# points the contract leaves open
# ---------------------------------------------------------------------------

def _eng(scenario, words, *, owned=3, tx_words=(), rx_plans=(), rival=None):
    return gen.EngineCase(0, scenario, list(words), owned, list(tx_words), list(rx_plans), rival)


def _open_point_probes():
    """(name, Interpretation field(s), candidate values, engine program)."""
    I, M = gen.ins, gen.imm
    P = lambda clock, tx, rx: M("PINS", ref.Pins(clock, tx, rx).encode())  # noqa: E731
    LINE, DRIVE, SAMPLE, MSB, CRCB = gen.LINE, gen.DRIVE, gen.SAMPLE, gen.MSB, gen.CRCBIT
    tail = [I("CRC", 1, 0, 2), I("PUSH"), I("LSTAT", 1), I("PUSH"), I("HALT")]
    nrz = ref.Lcfg(ref.NRZ, False, False, 1, False, False, False, 0)
    probes = []
    # a line XFER issued in the cycle of the tick it needs
    probes.append(("drive XFER issued in a boundary-tick cycle", "first_tick_strict", (True, False), _eng(
        "tx", [P(1, 0, 0), M("DIR", 3), M("LCFG", 0), M("LOAD", 0b1011), M("LTIM", 4 | 4 << 16),
               I("NOP"), I("NOP"), I("NOP"), I("XFER", 4, 0, LINE | DRIVE)] + tail)))
    probes.append(("sample XFER issued in a mid-bit-tick cycle", "first_tick_strict", (True, False), _eng(
        "rx", [P(1, 0, 0), M("DIR", 0), M("LCFG", 0), M("LOAD", 0), M("LTIM", 4 | 4 << 16)]
        + [I("NOP")] * 7 + [I("XFER", 6, 0, LINE | SAMPLE), I("PUSH")] + tail,
        rx_plans=[gen.RxPlan([1, 0, 0, 1, 1, 0])])))
    # LSTAT[5] after sampling
    probes.append(("LSTAT line level after an NRZ sample", "lstat_level_from_samples", (True, False), _eng(
        "rx", [P(1, 0, 0), M("DIR", 0), M("LCFG", 0), M("LTIM", 3), I("XFER", 4, 0, LINE | SAMPLE)] + tail,
        rx_plans=[gen.RxPlan([0, 1, 0, 1])])))
    # NRZI receive after an NRZI drive without LCFG
    nrzi0 = ref.Lcfg(ref.NRZI, False, False, 1, False, False, False, 0).encode()
    probes.append(("NRZI previous sample after a drive-only XFER", "drive_only_sets_prev_sample", (True, False),
                   _eng("rx", [P(1, 0, 0), M("DIR", 1), M("LCFG", nrzi0), M("LTIM", 4), M("LOAD", 0),
                               I("XFER", 3, 0, LINE | DRIVE), I("XFER", 4, 0, LINE | SAMPLE), I("PUSH")] + tail,
                        rx_plans=[gen.RxPlan([1, 1, 0, 1])])))
    # a wrong received stuff bit followed by more data
    either3 = ref.Lcfg(ref.NRZ, True, True, 3, False, False, False, 0).encode()
    probes.append(("run counter after a wrong stuff bit (either polarity)", "stuff_error_state",
                   ("as_expected", "received", "reset", "count"),
                   _eng("rx", [P(1, 0, 0), M("DIR", 0), M("LCFG", either3), M("LTIM", 3),
                               I("XFER", 12, 0, LINE | SAMPLE | CRCB), I("PUSH")] + tail,
                        rx_plans=[gen.RxPlan([1, 1, 1, 1, 1, 0, 1, 1, 0, 0, 1, 0], stuff_error=True,
                                             error_cell=0)])))
    ones3 = ref.Lcfg(ref.NRZ, True, False, 3, False, False, False, 0).encode()
    probes.append(("run counter after a wrong stuff bit (runs of 1s)", "stuff_error_state",
                   ("as_expected", "received", "reset", "count"),
                   _eng("rx", [P(1, 0, 0), M("DIR", 0), M("LCFG", ones3), M("LTIM", 3),
                               I("XFER", 12, 0, LINE | SAMPLE | CRCB), I("PUSH")] + tail,
                        rx_plans=[gen.RxPlan([1, 1, 1, 1, 1, 0, 1, 1, 1, 0, 1, 0], stuff_error=True,
                                             error_cell=0)])))
    # SE0: which pins, and whether the pair bit is needed
    usb = ref.Lcfg(ref.NRZI, True, False, 6, True, False, True, 1).encode()
    probes.append(("SE0 pins with the RX field != the data field", "se0_pins", ("rx_pair", "data_pair"),
                   _eng("rx", [P(1, 2, 0), M("DIR", 0), M("LCFG", usb), M("LTIM", 4),
                               I("XFER", 8, 0, LINE | SAMPLE), I("PUSH")] + tail,
                        rx_plans=[gen.RxPlan([1, 0, 1, 1, 0, 0, 1, 0], se0_at=5)])))
    se0_nopair = ref.Lcfg(ref.NRZ, False, False, 1, False, False, True, 1).encode()
    probes.append(("SE0 end with LCFG pair clear", "se0_needs_pair", (True, False),
                   _eng("rx", [P(1, 0, 0), M("DIR", 0), M("LCFG", se0_nopair), M("LTIM", 4),
                               I("XFER", 8, 0, LINE | SAMPLE), I("PUSH")] + tail,
                        rx_plans=[gen.RxPlan([1, 0, 1, 1, 0, 0, 1, 0], se0_at=3)])))
    pair_nrz = ref.Lcfg(ref.NRZ, False, False, 1, True, False, False, 0).encode()
    probes.append(("sample-only XFER with an unowned pair pin", "pair_rule_without_drive", (True, False),
                   _eng("rx", [P(1, 0, 0), M("DIR", 0), M("LCFG", pair_nrz), M("LTIM", 4),
                               I("XFER", 4, 0, LINE | SAMPLE), I("PUSH")] + tail, owned=1,
                        rx_plans=[gen.RxPlan([1, 0, 1, 1])])))
    # drive and sample after a lost arbitration: stuffing and CRC basis
    can = ref.Lcfg(ref.NRZ, True, True, 5, False, True, False, 1).encode()
    ours = [0, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1, 1, 0, 1]
    rival = [0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0]
    word = ref.bits_to_int(ours, lsb_first=False) << 16
    probes.append(("drive-and-sample after a lost arbitration: stuffing and CRC bits",
                   ("ds_stuff_basis", "ds_crc_basis"),
                   tuple((a, b) for a in ("received", "sent") for b in ("received", "sent", "tx")),
                   _eng("loop", [P(1, 0, 0), M("SET", 1), M("DIR", 1), M("LCFG", can), M("LTIM", 6),
                                 I("CRC", 0, 2, 3), M("LOAD", word >> 16), I("SHL", 0, 0, 16),
                                 I("XFER", 16, 0, LINE | DRIVE | SAMPLE | MSB | CRCB), I("PUSH")] + tail,
                        rival=gen.RivalPlan(rival))))
    ours = [0, 0, 0, 0, 0, 0, 1, 1]
    probes.append(("arbitration check in a stuff cell", "arbitration_in_stuff", (True, False), _eng(
        "loop", [P(1, 0, 0), M("SET", 1), M("DIR", 1), M("LCFG", can), M("LTIM", 6),
                 M("LOAD", ref.bits_to_int(ours, lsb_first=False) << 8), I("SHL", 0, 0, 16),
                 I("XFER", 8, 0, LINE | DRIVE | SAMPLE | MSB), I("PUSH")] + tail,
        rival=gen.RivalPlan([0, 0, 0, 0, 0, 0, 0, 1, 1], raw=True))))
    arb = ref.Lcfg(ref.NRZ, False, False, 1, False, True, False, 1).encode()
    probes.append(("drive level in the XFER after a lost arbitration", "lost_persists", (True, False), _eng(
        "loop", [P(1, 0, 0), M("SET", 1), M("DIR", 1), M("LCFG", arb), M("LTIM", 6),
                 M("LOAD", 0xF0F0), I("SHL", 0, 0, 16), I("XFER", 8, 0, LINE | DRIVE | SAMPLE | MSB),
                 M("LOAD", 0x0000), I("XFER", 8, 0, LINE | DRIVE | SAMPLE | MSB), I("PUSH")] + tail,
        rival=gen.RivalPlan([1, 1, 1, 0, 0, 0, 0, 0]))))
    either2 = ref.Lcfg(ref.NRZ, True, True, 2, False, False, False, 1).encode()
    probes.append(("LCFG initial level as a previous bit of the stuffing run", "init_level_counts_in_run",
                   (True, False), _eng("tx", [P(1, 0, 0), M("DIR", 3), M("LCFG", either2), M("LTIM", 3),
                                              M("LOAD", 0b0101), I("XFER", 4, 0, LINE | DRIVE)] + tail)))
    probes.append(("LCFG bit 3 with run length 1 and stuffing off", "either_len1_invalid_without_stuffing",
                   (True, False), _eng("tx", [P(1, 0, 0), M("DIR", 3), M("LCFG", 0b1000), M("LTIM", 3),
                                              M("LOAD", 0b0110), I("XFER", 4, 0, LINE | DRIVE)] + tail)))
    probes.append(("classic XFER, drive and sample, CRC bit", "classic_ds_crc_basis", ("sent", "received"),
                   _eng("tx", [P(1, 0, 2), M("DIR", 3), I("CRC", 0, 3, 3), M("LOAD", 0xA5C3),
                               I("XFER", 16, 2, 0x40 | 0x10 | 0x08)] + tail)))
    man = ref.Lcfg(ref.MANCHESTER, False, False, 1, True, False, False, 0).encode()
    probes.append(("LCFG before the second half of a Manchester cell", "lcfg_cancels_pending_half",
                   (True, False), _eng("tx", [P(1, 0, 0), M("DIR", 3), M("LCFG", man), M("LTIM", 6),
                                              M("LOAD", 0b1), I("XFER", 1, 0, LINE | DRIVE), M("LCFG", man),
                                              M("WAIT", 20)] + tail)))
    probes.append(("LTIM before the second half of a Manchester cell", "ltim_pending_half",
                   ("keep", "cancel", "next_mid"), _eng("tx", [P(1, 0, 0), M("DIR", 3), M("LCFG", man), M("LTIM", 6),
                                              M("LOAD", 0b1), I("XFER", 1, 0, LINE | DRIVE), M("LTIM", 6),
                                              M("WAIT", 20)] + tail)))
    return probes


def _reading(fields, value) -> ref.Interpretation:
    if isinstance(fields, tuple):
        return dataclasses.replace(RTL_READING, **dict(zip(fields, value)))
    return dataclasses.replace(RTL_READING, **{fields: value})


@cocotb.test(skip=SKIP)
async def test_contract_open_points(dut):
    """Directed programs for each point docs/isa.md leaves open: which reading the RTL follows (logged; RTL_READING must be one of them)."""
    chip = Chip(dut)
    await chip.begin()
    report, unexplained, inconsistent = [], [], []
    for name, fields, candidates, engine in _open_point_probes():
        matches = []
        for value in candidates:
            try:
                problems = await run_case(chip, gen.Case(0, [engine]), _reading(fields, value))
            except Exception as error:  # noqa: BLE001 - a reading the schedule cannot run
                problems = [f"prediction failed: {error!r}"]
            if not problems:
                matches.append(value)
            elif VERBOSE:
                dut._log.info("open point %r, reading %r: %s", name, value, problems[:3])
        current = tuple(getattr(RTL_READING, f) for f in fields) if isinstance(fields, tuple) \
            else getattr(RTL_READING, fields)
        report.append(f"{name}: {fields} -> RTL matches {matches} (RTL_READING {current})")
        if not matches:
            unexplained.append(name)
        elif current not in matches:
            inconsistent.append(name)
    for line in report:
        dut._log.info("open point: %s", line)
    assert not unexplained, f"no reading explains the RTL: {unexplained}"
    assert not inconsistent, f"RTL_READING disagrees with the RTL: {inconsistent}"
