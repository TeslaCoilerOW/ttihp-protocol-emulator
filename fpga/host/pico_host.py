# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Raspberry Pi Pico (MicroPython) host for the protocol emulator's pin port.

The host clocks the design itself, like the Tiny Tapeout demo board, where
the RP2040/RP2350 drives the project clock: for every cycle it sets ui_in
(and rst_n/ena) while the clock is low, samples uo_out (the pre-edge value,
including the combinational window-change bubble), then pulses the clock.
This is the cycle discipline of docs/isa.md ("Host shares clk and respects
synchronous input timing") and of test/harness.py.

Use with the CLOCK=host FPGA builds (docs/fpga.md, "Pico host"). The same
file runs under CPython with any port object that has
step(ui, reset, enabled) -> uo; fpga/host/test_pico_host.py runs it against
the Python reference model and fpga/sim/test_fpga_pico.py against the
simulated FPGA top.

MicroPython, on the Pico (copy this file and the firmware JSON you need):

    from pico_host import PicoPort, NibbleHost
    host = NibbleHost(PicoPort())          # default wiring, see PicoPort
    host.reset()
    print(host.status(7))                  # ISA version: 2
    host.load(0, [0x030000FF, 0x020000A5, 0x05000002], ownership=0xFF)
    host.command(4, 1)                     # START engine 0: pins show a5
"""

# Host commands and READ_SELECT values (docs/isa.md).
SELECT, BEGIN, COMMIT, OWN, START, STOP, ROUTE, CLEAR = 0, 1, 2, 3, 4, 5, 6, 7
READ_SELECT, EVENT, FLUSH, TRIGGER = 8, 9, 10, 11
RS_STATUS, RS_TIMESTAMP, RS_LEVELS, RS_PC, RS_EVENT, RS_COUNT, RS_HELD_RX, RS_VERSION = 0, 1, 2, 3, 4, 5, 6, 7
UI_WVALID, UI_RREADY = 0x10, 0x20
UO_WREADY, UO_RVALID, UO_IRQ, UO_FAULT = 0x10, 0x20, 0x40, 0x80


class HostTimeout(Exception):
    pass


class PicoPort:
    """GPIO wiring of a Pico to a CLOCK=host FPGA build (all 3.3 V).

    ui:  8 GPIOs driving ui_in[0..7]
    uo:  GPIOs reading uo_out[0..n-1] (at least 6: nibble, write-ready,
         read-valid; the Urbana build only brings out uo_out[5:0])
    clk: GPIO driving the design clock (the FPGA's host_clk pin)
    rst: GPIO driving reset; rst_active_high=True for the Urbana build
         (servo pin, active high), False for the Cmod A7 (rst_n)
    ena: GPIO driving ena, or None (the FPGA pulls ena high / uses a switch)
    Default: Cmod A7 host-clock build, GP0-7 -> ui, GP8-15 <- uo,
    GP16 -> clk, GP17 -> rst_n, GP18 -> ena.
    """

    def __init__(self, ui=(0, 1, 2, 3, 4, 5, 6, 7), uo=(8, 9, 10, 11, 12, 13, 14, 15),
                 clk=16, rst=17, rst_active_high=False, ena=18):
        from machine import Pin
        self._ui = [Pin(n, Pin.OUT, value=0) for n in ui]
        self._uo = [Pin(n, Pin.IN) for n in uo]
        self._clk = Pin(clk, Pin.OUT, value=0)
        self._rst = Pin(rst, Pin.OUT, value=1 if rst_active_high else 0)
        self._rst_high = rst_active_high
        self._ena = Pin(ena, Pin.OUT, value=1) if ena is not None else None

    def step(self, ui, reset=False, enabled=True):
        for i, pin in enumerate(self._ui):
            pin.value(ui >> i & 1)
        self._rst.value(1 if reset == self._rst_high else 0)
        if self._ena is not None:
            self._ena.value(1 if enabled else 0)
        uo = 0
        for i, pin in enumerate(self._uo):
            uo |= pin.value() << i
        self._clk.value(1)
        self._clk.value(0)
        return uo


class NibbleHost:
    """Host protocol over a cycle port (same operations as test/harness.py)."""

    def __init__(self, port, timeout=100000):
        self.port = port
        self.window = 0
        self.timeout = timeout
        self.cycles = 0

    def step(self, ui=None, reset=False, enabled=True):
        if ui is None:
            ui = self.window << 6
        self.cycles += 1
        return self.port.step(ui, reset, enabled)

    def idle(self, count=1):
        for _ in range(count):
            self.step()

    def reset(self, cycles=4):
        self.window = 0
        for _ in range(cycles):
            self.step(0, reset=True)

    def set_window(self, window):
        if self.window != window:
            self.window = window
            self.step()

    def write(self, window, word):
        if window not in (0, 1, 2) or not 0 <= word < 1 << 32:
            raise ValueError("invalid host write")
        self.set_window(window)
        for nibble in range(8):
            ui = window << 6 | UI_WVALID | (word >> (4 * nibble) & 15)
            for _ in range(self.timeout):
                if self.step(ui) & UO_WREADY:
                    break
            else:
                self.set_window(window ^ 1)   # abandon the partial word
                raise HostTimeout("write-ready stayed low (window %d)" % window)
        self.step()

    def read(self, window=0):
        if window not in (0, 3):
            raise ValueError("invalid host read")
        self.set_window(window)
        result = 0
        for nibble in range(8):
            for _ in range(self.timeout):
                uo = self.step(window << 6 | UI_RREADY)
                if uo & UO_RVALID:
                    result |= (uo & 15) << (4 * nibble)
                    break
            else:
                self.set_window(window ^ 1)
                raise HostTimeout("read-valid stayed low (window %d)" % window)
        return result

    def command(self, opcode, payload=0):
        if not 0 <= opcode <= 255 or not 0 <= payload < 1 << 24:
            raise ValueError("invalid host command")
        self.write(0, opcode << 24 | payload)

    def status(self, selection=RS_STATUS):
        """READ_SELECT, then read window 0 after a window bounce (fresh snapshot)."""
        self.command(READ_SELECT, selection)
        self.window = 1
        self.step()
        return self.read(0)

    def load(self, engine, words, ownership=0, open_drain=0):
        self.command(SELECT, engine)
        self.command(BEGIN)
        self.command(OWN, ownership | open_drain << 8)
        count = 0
        for word in words:
            self.write(1, word)
            count += 1
        self.command(COMMIT, count)

    def load_image(self, image):
        """image: a parsed firmware/<name>.image.json (json.load on the Pico)."""
        self.load(image["engine"], image["words"], ownership=image["owned_pins"],
                  open_drain=image["open_drain"])

    def push_tx(self, engine, words):
        self.command(SELECT, engine)
        for word in words:
            self.write(2, word)

    def pop_rx(self, engine, count):
        self.command(SELECT, engine)
        return [self.read(3) for _ in range(count)]

    def route(self, source, destination, count):
        self.command(ROUTE, source | destination << 2 | 16 | count << 5)

    def engine_status(self, engine):
        self.command(SELECT, engine)
        status = self.status(RS_STATUS)
        levels = self.status(RS_LEVELS)
        return {"running": status & 1, "committed": status >> 1 & 1, "stalled": status >> 2 & 1,
                "fault": status >> 8 & 0xFF if status & 8 else 0,
                "tx_level": levels & 0xFFFF, "rx_level": levels >> 16}


def loopback(host, images, message=b"PICO OK!", log=print):
    """The pe_host.py loopback test over the pin port (jumpers uio0-uio1 and
    uio3-uio4). images: dict name -> parsed image JSON for uart-tx, uart-rx,
    spi-controller-mode0. Protocol rates scale with the host's clock rate."""
    words = list(message[:8])
    host.reset()
    for name in ("uart-tx", "uart-rx", "spi-controller-mode0"):
        host.load_image(images[name])
    host.push_tx(0, words)
    host.route(1, 2, len(words))
    host.command(START, 0b0111)
    # 8 frames x 10 bits x 64 clocks, plus SPI and slack
    host.idle(8 * 10 * 64 + 2000)
    host.command(STOP, 0b0111)
    uart = host.engine_status(1)
    spi = host.engine_status(2)
    got = host.pop_rx(2, spi["rx_level"])
    log("engine 2 received %r" % bytes([w & 0xFF for w in got]))
    ok = got == words and uart["fault"] in (0, 3) and spi["fault"] == 0
    log("LOOPBACK PASS" if ok else "LOOPBACK FAIL (uart %r spi %r)" % (uart, spi))
    return ok
