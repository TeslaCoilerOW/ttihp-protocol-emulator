# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""High-level demo: firmware/flagship-scenario.json on any backend.

Engine 0 transmits UART on pin 0, engine 1 receives UART on pin 1, engine 2
is an SPI mode-0 controller on pins 2-5, engine 3 an I2C controller on the
open-drain pins 6/7, and a ROUTE moves every UART byte received by engine 1
into engine 2's TX FIFO with no host involvement.

peers="software": pe_host.peers play the UART sender/receiver, SPI target and
I2C target in lockstep with the host-supplied clock (model backend, or an RP2
whose uio GPIOs are wired to the chip's uio pins: nothing else attached).
peers="external": real peripherals are wired to uio; the engines free-run
(PWM clock when the port supports it) and the host reports what it reads back.
Engine 1's bounded idle wait (fault 3) is expected in that mode; the host
clears it after STOP to read the sticky host fault from uo[7].

The host sequence equals test/scenarios.py flagship(): reset (4 cycles), per
image an ISA-version read and SELECT/BEGIN/OWN/words/COMMIT, TX prefill,
ROUTE, START, the UART sender starting 96 cycles after START, completion, 64
settle cycles, STOP, SELECT 2 and eight RX reads (MicroPython compatible).
"""

from .errors import EngineFault, HostTimeout, ImageError
from .image import FirmwareImage, join, load_scenario
from .peers import Environment, I2CTarget, SpiTarget, UartMonitor, UartSource
from .protocol import UO_FAULT, W_TX

UART_START_OFFSET = 96
SETTLE_CYCLES = 64


def _dirname(path):
    index = path.rfind("/")
    return path[:index] if index > 0 else ("/" if index == 0 else "")


class FlagshipPeers:
    """Software peers wired as the scenario's pin_connections describe."""

    def __init__(self, scenario, i2c_stretch=0):
        stimulus = scenario["stimulus"]
        pins = {}
        for connection in scenario["pin_connections"]:
            pins[connection["signal"]] = connection["pin"]
        bit_cycles = stimulus["uart_bit_cycles"]
        self.uart_in = UartSource(pins["uart_rx"], stimulus["uart_rx_words"], bit_cycles,
                                  stimulus["uart_interframe_idle_cycles"])
        self.uart_out = UartMonitor(pins["uart_tx"], bit_cycles)
        self.spi = SpiTarget(0, [stimulus["spi_return_word"]], sck=pins["spi_sck"],
                             mosi=pins["spi_mosi"], miso=pins["spi_miso"], cs=pins["spi_csn"])
        self.i2c = I2CTarget(address=stimulus["i2c_ack_address"], scl=pins["i2c_scl"],
                             sda=pins["i2c_sda"], stretch_cycles=i2c_stretch)
        self.env = Environment([self.uart_in, self.spi, self.i2c, self.uart_out], pullups=0xFF)


class FlagshipResult:
    def __init__(self):
        self.passed = False
        self.mismatches = []
        self.cycles_to_complete = None
        self.total_cycles = 0
        self.uart_tx_words = None
        self.spi_mosi_words = None
        self.i2c_bytes = None
        self.i2c_stops = None
        self.engine2_rx_words = None
        self.levels = {}
        self.fault_report = None
        self.fault_report_after_clear = None
        self.host_fault = None
        self.notes = []
        self.peers = None

    def expect(self, name, got, want):
        if got != want:
            self.mismatches.append("%s: got %r, expected %r" % (name, got, want))

    def summary(self):
        lines = ["flagship scenario: %s" % ("PASS" if self.passed else "FAIL")]
        if self.cycles_to_complete is not None:
            lines.append("  cycles from START to completion: %d" % self.cycles_to_complete)
        lines.append("  total host cycles: %d" % self.total_cycles)
        for name in ("uart_tx_words", "spi_mosi_words", "i2c_bytes", "engine2_rx_words"):
            value = getattr(self, name)
            if value is not None:
                lines.append("  %s: %s" % (name, " ".join("%02x" % v for v in value)))
        if self.levels:
            lines.append("  queue levels (tx, rx): %r" % (self.levels,))
        lines.append("  faults: %r" % (self.fault_report,))
        if self.fault_report_after_clear is not None:
            lines.append("  faults after clearing engine 1: %r" % (self.fault_report_after_clear,))
        for item in self.notes:
            lines.append("  note: " + item)
        for item in self.mismatches:
            lines.append("  MISMATCH " + item)
        return "\n".join(lines)


def load_flagship(pe, scenario_path, firmware_dir=None):
    scenario = load_scenario(scenario_path, pe.architecture)
    if firmware_dir is None:
        firmware_dir = _dirname(scenario_path)
    images = []
    for entry in scenario["images"]:
        base = entry["source"]
        if base.endswith(".source.json"):
            base = base[:-len(".source.json")]
        image = FirmwareImage.load(join(firmware_dir, base + ".image.json"),
                                   architecture=pe.architecture)
        if image.engine != entry["engine"]:
            raise ImageError("%s is assembled for engine %d, the scenario puts it on %d"
                             % (image.name, image.engine, entry["engine"]))
        images.append(image)
    return scenario, images


def run_flagship(pe, scenario_path="firmware/flagship-scenario.json", firmware_dir=None,
                 peers="software", i2c_stretch=0, done=None, max_cycles=None,
                 free_run_cycles=200000, free_run_hz=None, on_reset=None, log=None):
    """Run the flagship scenario; returns a FlagshipResult (result.passed).

    peers: "software" (pe_host.peers in lockstep), "external" (real
    peripherals, free-running clock) or an object shaped like FlagshipPeers
    (uart_in/uart_out/spi/i2c and an optional env), e.g. the test/ peers.
    done: optional completion predicate (default: every software peer saw its
    expected traffic). on_reset: optional callable run right after the reset.
    log: optional print-like callable for progress.
    """
    scenario, images = load_flagship(pe, scenario_path, firmware_dir)
    expected = scenario["expected"]
    result = FlagshipResult()
    pe.reset()
    if on_reset is not None:
        on_reset()
    if peers == "software":
        handles = FlagshipPeers(scenario, i2c_stretch)
    elif peers == "external":
        handles = None
    elif hasattr(peers, "uart_in"):
        handles = peers
    else:
        raise ValueError("peers must be 'software', 'external' or a FlagshipPeers-like object")
    if handles is not None:
        env = getattr(handles, "env", None)
        if env is not None:
            pe.port.env = env
        result.peers = handles
    for image in images:
        pe.load_image(image)
        if log:
            log("loaded %r" % (image,))
    for prefill in scenario["prefill_tx"]:
        pe.select(prefill["engine"])
        for word in prefill["words"]:
            pe.write_word(W_TX, word)
    for route in scenario["routes"]:
        pe.route(route["source_engine"], route["destination_engine"], route["word_count"])
    pe.start(scenario["start_mask"])
    started = pe.cycles
    total = len(expected["spi_mosi_words"])
    if handles is not None:
        handles.uart_in.start = pe.cycles + UART_START_OFFSET
        if done is None:
            def done():
                return (len(handles.uart_out.words) >= len(expected["uart_tx_words"])
                        and len(handles.spi.received) >= total and handles.i2c.stops >= 1)
        if max_cycles is None:
            max_cycles = (UART_START_OFFSET + len(handles.uart_in.words)
                          * handles.uart_in.frame_cycles + 2000)
        cycle = pe.cycle
        for _ in range(max_cycles):
            if done():
                break
            if cycle() & UO_FAULT:
                report = pe.fault_report()
                raise EngineFault("flagship: FAULT pin high at cycle %d: %r"
                                  % (pe.cycles, report), report)
        else:
            raise HostTimeout("flagship scenario did not complete in %d cycles" % max_cycles)
        result.cycles_to_complete = pe.cycles - started
        pe.idle(SETTLE_CYCLES)
        if log:
            log("completed %d cycles after START" % result.cycles_to_complete)
    else:
        pe.free_run(free_run_cycles, free_run_hz)
    pe.stop(scenario["start_mask"])
    pe.select(2)
    if handles is not None:
        result.engine2_rx_words = [pe.rx_read() for _ in range(total)]
    else:
        result.engine2_rx_words = pe.rx_drain()
    for engine in range(pe.engines):
        result.levels[engine] = pe.levels(engine)
    result.fault_report = pe.fault_report()
    result.total_cycles = pe.cycles
    if handles is not None:
        result.uart_tx_words = handles.uart_out.words
        result.spi_mosi_words = handles.spi.received
        result.i2c_bytes = handles.i2c.received
        result.i2c_stops = handles.i2c.stops
        result.expect("uart_tx_words", handles.uart_out.words, expected["uart_tx_words"])
        result.expect("uart framing errors", handles.uart_out.errors, [])
        result.expect("spi_mosi_words", handles.spi.received, expected["spi_mosi_words"])
        result.expect("spi violations", handles.spi.violations, [])
        result.expect("i2c_write_words", handles.i2c.received, expected["i2c_write_words"])
        result.expect("i2c STOP count", handles.i2c.stops, 1)
        result.expect("i2c violations", getattr(handles.i2c, "violations", []), [])
        result.expect("engine2_rx_words", result.engine2_rx_words, expected["engine2_rx_words"])
        result.expect("route source RX level (engine 1)", result.levels[1][1], 0)
        result.expect("route destination TX level (engine 2)", result.levels[2][0], 0)
    report = result.fault_report
    if handles is not None:
        result.expect("faults", report.ok, True)
        result.host_fault = report.host_fault
    else:
        _check_external_faults(pe, result, report)
    if report.fault_pin is None:
        result.notes.append("sticky host fault not checked: uo[7] is not wired to this host")
    result.passed = not result.mismatches
    if log:
        log(result.summary())
    return result


def _check_external_faults(pe, result, report):
    """Fault check for peers="external".

    The uart-rx image bounds its idle and start-bit waits at twelve bit
    periods (docs/firmware.md), so engine 1 ends in fault 3 whenever the UART
    line stays idle that long, which with real peripherals is always the case
    before STOP. That fault is expected; any other engine fault fails.
    uo[7] is the OR of the sticky host fault and every engine fault, so it
    says nothing about the host fault while engine 1 is faulted: CLEAR engine
    1 (halted by the STOP before this check, and CLEAR is accepted for a
    halted engine whatever the host fault) and read the pin again.
    result.fault_report keeps the report from before the CLEAR.
    """
    unexpected = [s for s in report.faulted if not (s.engine == 1 and s.fault == 3)]
    result.expect("unexpected faults", [repr(s) for s in unexpected], [])
    if unexpected:
        return
    after = report
    if report.faulted:
        pe.clear(1 << 1)
        after = result.fault_report_after_clear = pe.fault_report()
        result.expect("faults after clearing engine 1", [repr(s) for s in after.faulted], [])
    result.host_fault = after.host_fault
    if after.fault_pin is not None:
        result.expect("host fault", after.host_fault, False)
