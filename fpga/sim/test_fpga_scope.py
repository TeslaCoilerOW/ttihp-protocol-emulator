# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""On-board capture unit (pe_fpga_scope.v) in the full UART-bridge FPGA top.

The board top (tb_fpga_bridge.v) is driven only through its USB-UART pins by
fpga/host/pe_host.py and fpga/host/pe_scope.py, as on hardware. An
independent monitor (EdgeMonitor) samples the channels' source signals after
every rising clock edge in the simulator; each test compares the edges the
host tool reconstructs from the capture unit's records with the monitor's,
edge for edge and cycle for cycle.

    make FPGA_TB=bridge COCOTB_TEST_MODULES=test_fpga_scope                       # Cmod A7 pll50
    make FPGA_TB=bridge FPGA_BOARD=urbana COCOTB_TEST_MODULES=test_fpga_scope     # Urbana pll50
    make FPGA_TB=bridge EXTRA_DEFINES=-DPE_CLOCK_OSC FPGA_SIM_CLOCK_NS=83.333 \\
         COCOTB_TEST_MODULES=test_fpga_scope                                      # Cmod A7 osc12
    FPGA_SCOPE_LONG=1 ...    also the marker / 22-bit stamp wrap test (4.5 M cycles)
    make ... FPGA_NETLIST=build/synth_netlist.v   the synthesized netlist of a bridge build

Edge indices are absolute: the monitors of all tests share one count of the
board clock's rising edges since t = 0 (each test's clock starts low, and no
edge occurs between tests). In an RTL run the monitor also checks that the
capture unit's stamp register equals that count minus 2. With FPGA_NETLIST
the design hierarchy is flattened: the monitor then sees the pads and the
board top's kept uio_out/uio_oe wires only, and the uo_out/ui_in test is
skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge, Timer

import pe_host
import pe_scope
from bridge_sim import SimTransport, SimUart, run, wait_ns

REPO = Path(__file__).resolve().parents[2]
DEMO_FIRMWARE = REPO / "demo" / "firmware"
CLOCK_NS = float(os.environ.get("FPGA_SIM_CLOCK_NS", "20"))
URBANA = os.environ.get("FPGA_BOARD") == "urbana"
PROBE = (1 << 6) | (1 << 7)
PROBE_PERIOD = 344
NETLIST = bool(os.environ.get("FPGA_NETLIST"))
EDGES = [0]          # rising edges of the board clock since t = 0, over all tests


class EdgeMonitor:
    """Ground truth. After every rising edge of the board clock (absolute edge
    index n, EDGES), sample the channel sources: the protocol pads (tb),
    uio_out/uio_oe (board top), uo_out and the applied ui_in (shell; RTL
    only). A change seen after edge n was launched by edge n. ``offset`` is
    the difference between the capture unit's stamp register and n - 2 at the
    first sample (RTL only); it must be 0."""

    def __init__(self, dut) -> None:
        self.dut = dut
        top = dut.dut
        self.pads = dut.pads
        self.out, self.oe = top.uio_out, top.uio_oe
        if NETLIST:
            self.uo = self.ui = self.stamp = None
        else:
            self.uo, self.ui = top.shell.uo, top.shell.ui
            self.stamp = top.shell.g_bridge.g_scope.scope.stamp
        self.offset = None
        self.prev = None
        self.changes: list[tuple[int, str, int, int]] = []   # (edge, view, bit, level)
        self.active = False

    def _sample(self):
        pads, out, oe = int(self.pads.value), int(self.out.value), int(self.oe.value)
        core = (oe & out) | (~oe & pads & 0xFF)
        cur = {"pad": pads, "core": core}
        if self.uo is not None:
            cur.update(uo=int(self.uo.value), ui=int(self.ui.value))
        return cur

    async def run(self) -> None:
        while True:
            await RisingEdge(self.dut.clk)
            EDGES[0] += 1
            n = EDGES[0]
            if not self.active:
                self.prev = None
                continue
            await ReadOnly()
            if self.offset is None and self.stamp is not None:
                self.offset = (int(self.stamp.value) - (n - 2)) % (1 << 48)
            cur = self._sample()
            if self.prev is not None:
                edge = n
                for view, value in cur.items():
                    d = value ^ self.prev[view]
                    for bit in range(8):
                        if d >> bit & 1:
                            self.changes.append((edge, view, bit, value >> bit & 1))
            self.prev = cur

    def edges(self, channel: int, view: str, lo: int, hi: int) -> list[tuple[int, int]]:
        """Changes of one scope channel in [lo, hi]; view 'pad' or 'core' for uio."""
        if channel < 8:
            key, bit = view, channel
        elif channel < 16:
            key, bit = "uo", channel - 8
        else:
            key, bit = "ui", channel - 16
        return [(e, lvl) for e, v, b, lvl in self.changes if v == key and b == bit and lo <= e <= hi]


async def setup(dut, monitor_active: bool = False):
    # start_high=False: the first rising edge comes half a period after t = 0.
    period_ps = round(CLOCK_NS * 1000)
    cocotb.start_soon(Clock(dut.clk, period_ps, unit="ps", period_high=period_ps // 2).start(start_high=False))
    dut.btn0.value = 0
    dut.jumpers.value = 0
    dut.host_ext.value = 0
    uart = SimUart(dut)
    monitor = EdgeMonitor(dut)
    monitor.active = monitor_active
    cocotb.start_soon(monitor.run())
    await Timer(2000, "ns")
    chip = pe_host.Chip(pe_host.Bridge(SimTransport(uart)))
    return chip, pe_scope.Scope(chip.b), monitor


def load_probe(chip) -> None:
    image = pe_host.load_image("timing-probe", firmware=DEMO_FIRMWARE)
    chip.load(image["engine"], image["words"], ownership=image["owned_pins"], open_drain=image["open_drain"])


def wait_finished(scope, poll_ns: float = 20_000, limit_ns: float = 50_000_000):
    waited = 0.0
    while True:
        st = scope.status()
        if st.finished or waited > limit_ns:
            return st
        wait_ns(poll_ns)
        waited += poll_ns


def compare(cap, monitor, dut) -> dict:
    """Scope edges == monitor edges, per enabled channel, inside the window."""
    lo, hi = cap.window()
    view = "core" if cap.status.core_view else "pad"
    counts = {}
    for ch in cap.enabled:
        got = cap.edges(ch)
        want = monitor.edges(ch, view, lo, hi)
        if got != want:
            first = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
            raise AssertionError(f"{pe_scope.CHANNELS[ch]} ({view}): scope {len(got)} edges, monitor "
                                 f"{len(want)}; first difference at #{first}: scope {got[first:first + 3]} "
                                 f"monitor {want[first:first + 3]}")
        counts[pe_scope.CHANNELS[ch]] = len(got)
    dut._log.info("window %d..%d (%d cycles): edges identical %s", lo, hi, hi - lo + 1, counts)
    return counts


@cocotb.test()
async def test_scope_basics(dut):
    """Identification, idle status, empty read-back, unknown command, forced
    trigger, pin-host mode. Runs first: the monitor starts with the
    simulation, so the stamp origin is checked absolutely (offset 0: the
    stamp after the n-th rising edge is n - 2)."""
    chip, scope, monitor = await setup(dut, monitor_active=True)

    def host():
        v = chip.b.version()
        c = chip.b.cycles()
        st = scope.status()
        empty = scope.dump(0, 10)
        try:
            chip.b.one(b"Z")
            unknown = None
        except pe_host.BridgeError as err:
            unknown = str(err)
        scope.arm(pe_scope.channel_mask("ui7"), pe_scope.channel_mask("ui7"), wait=True)
        waiting = scope.status()
        scope.force()
        scope.halt()
        forced = scope.collect(1e9 / CLOCK_NS)
        return v, st, empty, c, unknown, waiting, forced

    v, st, empty, c, unknown, waiting, forced = await run(host)
    dut._log.info("%s; %s; stamp - (edges - 2) = %s", v.describe(), st.describe(), monitor.offset)
    assert monitor.offset in (0, None), monitor.offset
    assert waiting.state == 1 and forced.status.forced and forced.status.halted, (waiting, forced.status)
    assert [r.kind for r in forced.records] == [pe_scope.K_START, pe_scope.K_END], forced.records
    aw = int(os.environ.get("FPGA_SCOPE_AW", "15" if URBANA else "14"))
    assert v.protocol == 2 and v.has_scope, v
    assert (st.version, st.channels, st.aw, st.state, st.records) == (1, 24, aw, 0, 0), st
    assert empty == [] and "unknown command" in unknown
    # stamp = bridge cycle counter - 2, and 'Q' is answered after 'C'.
    assert st.now + 2 >= c, (st.now, c)
    if os.environ.get("FPGA_BRIDGE_ONLY") == "1":
        return
    dut.host_ext.value = 1               # pin host owns the TT port: capture commands still work

    def pin_host():
        scope.arm(pe_scope.channel_mask("uio6,uio7"), limit=8)
        st = scope.status()
        scope.halt()
        try:
            chip.b.write(0, 0)
            refused = None
        except pe_host.BridgeError as err:
            refused = str(err)
        return st, scope.status(), refused

    st1, st2, refused = await run(pin_host)
    dut.host_ext.value = 0
    assert st1.state in (2, 3) and st2.state == 4 and st2.halted, (st1, st2)
    assert refused and "external pin host" in refused


@cocotb.test()
async def test_scope_probe_pad_and_core(dut):
    """Timing probe on engine 3: pad-view capture (trigger on the probe pins)
    and core-view capture; both equal the monitor edge for edge; both put
    every edge at the same phase of the 344-cycle frame."""
    chip, scope, monitor = await setup(dut)
    monitor.active = True
    limit = int(os.environ.get("FPGA_SCOPE_LIMIT", "256"))

    def host():
        chip.reset()
        load_probe(chip)
        scope.arm(PROBE, PROBE, wait=True, limit=limit)
        st0 = scope.status()
        chip.command(pe_host.START, 1 << 3)
        wait_finished(scope)
        pad = scope.collect(1e9 / CLOCK_NS)
        scope.arm(PROBE, 0, core_view=True, limit=limit)
        wait_finished(scope)
        core = scope.collect(1e9 / CLOCK_NS)
        chip.command(pe_host.STOP, 1 << 3)
        return st0, pad, core

    st0, pad, core = await run(host)
    assert st0.state == 1, st0.describe()                      # waiting for the trigger
    assert monitor.offset in (0, None), monitor.offset
    for cap in (pad, core):
        s = cap.summary()
        dut._log.info("%s view: %s", "core" if cap.status.core_view else "pad", s)
        assert cap.complete and cap.status.full and s["records"] == limit, s
        compare(cap, monitor, dut)
    assert pad.records[0].mask, "the trigger edge is in the start record"
    assert pad.status.forced is False and pad.records[0].kind == pe_scope.K_START
    phase = lambda cap: sorted({(r.stamp % PROBE_PERIOD, r.mask, r.values & PROBE)  # noqa: E731
                                for r in cap.records[1:-1] if r.mask})
    assert phase(pad) == phase(core), "pad and core views disagree on the frame phase"


@cocotb.test()
async def test_scope_overflow_and_halt(dut):
    """Buffer/limit full: end record last, FULL state, lost changes counted
    until the halt, nothing after it; records still equal the monitor's."""
    chip, scope, monitor = await setup(dut)
    monitor.active = True

    def host():
        chip.reset()
        load_probe(chip)
        chip.command(pe_host.START, 1 << 3)
        scope.arm(PROBE, 0, limit=24)
        st_full = wait_finished(scope)
        wait_ns(3000 * CLOCK_NS)
        st_later = scope.status()
        scope.halt()
        st_halt = scope.status()
        wait_ns(3000 * CLOCK_NS)
        st_after = scope.status()
        cap = scope.collect(1e9 / CLOCK_NS)
        chip.command(pe_host.STOP, 1 << 3)
        return st_full, st_later, st_halt, st_after, cap

    st_full, st_later, st_halt, st_after, cap = await run(host)
    for st in (st_full, st_later, st_halt, st_after):
        dut._log.info("%s", st.describe())
    assert st_full.state == 3 and st_full.full and st_full.records == 24
    assert st_later.lost > st_full.lost >= 0 and st_later.state == 3
    assert st_halt.state == 4 and st_halt.halted and st_after.lost == st_halt.lost
    assert cap.records[-1].kind == pe_scope.K_END and cap.status.end_stamp == cap.records[-1].stamp
    assert sum(1 for r in cap.records if r.kind == pe_scope.K_END) == 1
    compare(cap, monitor, dut)
    # Every probe edge after the end record up to the halt was counted as lost.
    lost_cycles = {e for e, v, b, _ in monitor.changes
                   if v == "pad" and b in (6, 7) and cap.status.end_stamp < e <= st_halt.now}
    assert st_halt.lost <= len(lost_cycles) and st_halt.lost > 0


@cocotb.test()
async def test_scope_readback_while_recording(dut):
    """Read-back while records are still being written (the probe writes one
    about every 8 cycles). With the single-port buffer (Urbana) a read that
    coincides with a write is repeated; the records read during recording
    must equal the same records read after the halt. In an RTL run the test
    also counts the repeated reads in the responder."""
    chip, scope, monitor = await setup(dut)
    single = os.environ.get("FPGA_BOARD") == "urbana"
    retries = [0]
    if not NETLIST:
        sc = dut.dut.shell.g_bridge.g_scope.scope

        async def count_retries():
            while True:
                await RisingEdge(dut.clk)
                await ReadOnly()
                if int(sc.rstate.value) == 5 and int(sc.uwait.value) == 0 and int(sc.rdata_ok.value) == 0:
                    retries[0] += 1

        cocotb.start_soon(count_retries())

    def host():
        chip.reset()
        load_probe(chip)
        chip.command(pe_host.START, 1 << 3)
        scope.arm(PROBE, 0)
        wait_ns(2000 * CLOCK_NS)
        st = scope.status()
        early = scope.dump(0, 64)
        during = scope.status()
        scope.halt()
        late = scope.dump(0, 64)
        chip.command(pe_host.STOP, 1 << 3)
        return st, early, during, late

    st, early, during, late = await run(host)
    dut._log.info("read 64 records while %d -> %d records were written; repeated reads %d (%s buffer)",
                  st.records, during.records, retries[0], "single-port" if single else "dual-port")
    assert st.state == 2 and during.records > st.records > 64, (st.describe(), during.describe())
    assert early == late and len(early) == 64
    pe_scope.decode(early, st.start_stamp)            # a valid record sequence
    if not NETLIST:
        assert (retries[0] > 0) if single else (retries[0] == 0), retries[0]


@cocotb.test(skip=NETLIST)
async def test_scope_host_port_channels(dut):
    """ui_in / uo_out channels: trigger on the host's write-valid, record the
    nibble handshakes of a status read, compare with the monitor."""
    chip, scope, monitor = await setup(dut)
    monitor.active = True
    en = pe_scope.channel_mask([f"ui{i}" for i in range(8)] + [f"uo{i}" for i in range(8)])

    def host():
        chip.reset()
        scope.arm(en, pe_scope.channel_mask("wvalid"), wait=True)
        isa = chip.status(pe_host.RS_VERSION)
        chip.command(pe_host.SELECT, 2)
        scope.halt()
        return isa, scope.collect(1e9 / CLOCK_NS)

    isa, cap = await run(host)
    assert isa == pe_host.ISA_VERSION
    counts = compare(cap, monitor, dut)
    # READ_SELECT + SELECT are two 8-nibble writes: at least 16 write-valid
    # pulses (a nibble is presented again if the core was not ready).
    assert counts["ui4"] >= 32 and counts["ui4"] % 2 == 0 and counts["uo5"] > 0, counts
    assert cap.activity[pe_scope.channel_index("ui4")] == counts["ui4"]


@cocotb.test(skip=os.environ.get("FPGA_SCOPE_LONG") != "1")
async def test_scope_markers_and_wrap(dut):
    """No channel enabled: the records are the start record, a marker at every
    multiple of 2^21 cycles and the end record; the capture spans a wrap of
    the 22-bit stamp field; the start stamp lies between two bridge 'C'
    readings (stamp = the bridge's cycle count)."""
    chip, scope, monitor = await setup(dut)

    def host():
        c0 = chip.b.cycles()
        scope.arm(0, 0)
        c1 = chip.b.cycles()
        st = scope.status()
        target = ((st.start_stamp >> 22) + 1 << 22) + (1 << 21) + 1000   # past a 2^22 boundary
        while scope.status().now < target:
            wait_ns(200_000 * CLOCK_NS)
        scope.halt()
        return c0, c1, scope.collect(1e9 / CLOCK_NS)

    c0, c1, cap = await run(host)
    kinds = [r.kind for r in cap.records]
    marks = [r.stamp for r in cap.records if r.kind == pe_scope.K_MARK]
    dut._log.info("start %d (C %d..%d), markers %s, end %d", cap.records[0].stamp, c0, c1, marks,
                  cap.records[-1].stamp)
    assert kinds[0] == pe_scope.K_START and kinds[-1] == pe_scope.K_END and set(kinds[1:-1]) == {pe_scope.K_MARK}
    assert c0 <= cap.records[0].stamp <= c1
    assert all(m % (1 << 21) == 0 for m in marks) and len(marks) >= 2
    assert all(b - a == 1 << 21 for a, b in zip(marks, marks[1:]))
    assert cap.records[0].stamp >> 22 != cap.records[-1].stamp >> 22      # crossed a 22-bit wrap
