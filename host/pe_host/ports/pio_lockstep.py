# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Lockstep PIO host port: a continuous project clock with host transactions
interleaved on the same RP2040/RP2350 PIO state machine (MicroPython compatible).

The other ports stop the chip's clock whenever the host talks to it: the host
supplies every edge (docs/isa.md: the host port is synchronous and ui_in is not
synchronised). Here one PIO state machine generates the project clock on its
side-set pin without pause and moves the host's nibble transfers on the same
clock, so the engines run at a steady rate while the host services queues.

Frame
-----
The program runs a fixed 6-PIO-cycle frame per project clock (slots 3, 4, 5
clock low, 0, 1, 2 clock high; slot 0 raises the clock):

    slot   3        4    5    0 (rising edge)   1    2
    clk    0        0    0    1                 1    1
    ui     changes  -    -    -                 -    -
    uo     -        -    -    -                 -    sampled (value from before the edge)

Every path through the program keeps this pattern (host/pio/pathcheck.py
checks all of them), so the project clock is f_sys / 6: 25 MHz at the RP2350's
150 MHz, 20.83 MHz at the RP2040's 125 MHz. ui_in only changes at slot 3,
half a period away from both rising edges. uo_out is read at slot 2 through
the RP2's two-flop input synchroniser, which returns the level at the rising
edge (before the chip can react to it).

Operations (one 32-bit TX FIFO word each; bits 4:0 are the handler address)
-------------------------------------------------------------------------
CYC   bits 12:5 = ui. Present ui for one frame; push [uo(8) | Y(24)].
WR    bits 12:5 = ui (write-valid set). Present ui and hold it until
      write-ready was sampled high (one extra frame per stall). No result.
RD    bits 12:5 = ui (read-ready set). Hold ui until read-valid was sampled
      high, then take the eight nibbles of the word in eight frames; push the
      32-bit word.
RDB   bits 12:5 and 20:13 = ui. One frame with ui (the window-change bubble,
      not sampled), then RD.
HALT  Stop the clock low until the next TX word (used around reset only);
      then push [uo | Y] like CYC.

Every operation is preceded by one fetch frame that drives write-valid and
read-ready low (keeping the window bits), and the fetch frame repeats while
the TX FIFO is empty. Idle and stall frames decrement Y, so each result
carries the count of those frames and the host knows the exact number of
rising edges (``port.count``) at every result.

Nothing here has run on hardware. docs/host.md ("Lockstep PIO port")
describes the rate, the timing margins, the pin maps and the limits.
"""

from ..errors import HostError, HostTimeout
from ..host import ProtocolEmulator
from ..protocol import FLUSH, ROUTE, UI_RREADY, UI_WVALID, UO_FAULT, W_RX
from .base import Port

# ------------------------------------------------------------------ program
FRAME_CYCLES = 6          # PIO cycles per project clock
ADDR_RDB = 7
ADDR_WR = 14
ADDR_HALT = 19
ADDR_CYC = 21
ADDR_FETCH = 24
ADDR_RD = 31
# frames per operation, beyond the fetch frame and the frames counted by Y
FIXED_FRAMES = {ADDR_CYC: 1, ADDR_WR: 1, ADDR_RD: 8, ADDR_RDB: 9, ADDR_HALT: 1}
# EXECCTRL.STATUS_N = 1 with STATUS_SEL = 0: MOV x, STATUS is all-ones while
# the TX FIFO is empty (same field value on RP2040 and RP2350).
EXECCTRL_STATUS = 1
# SMx_INSTR encodings used before the state machine starts (side-set bit 12).
INSTR_SET_Y_0 = 0xE040          # set y, 0      side 0
INSTR_JMP_FETCH = 0x0000 | ADDR_FETCH   # jmp fetch side 0

Y_BITS = 24
Y_MASK = (1 << Y_BITS) - 1


def lockstep_program():
    """The PIO program in MicroPython's rp2.asm_pio DSL. It fills all 32
    instruction slots, so it can only be loaded at offset 0: ``out pc``
    targets are absolute, and "rd" (address 31) continues at address 0 when
    the program counter wraps. Comments give each instruction's address and
    the frame slot(s) it occupies."""
    # 0, 1: RD branch table for ``out pc, 1`` on read-valid
    label("rv_low")
    jmp("hr_stall")             .side(1)         # 0  [0]
    label("rv_high")
    in_(x, 4)                   .side(1)         # 1  [0]  nibble 0 (sampled earlier)
    label("hr_n1")
    nop()                       .side(1)         # 2  [1]
    in_(pins, 4)                .side(1)         # 3  [2]  nibble k
    out(null, 4)                .side(0)[2]      # 4  [3-5] count nibbles in the OSR
    jmp(not_osre, "hr_n1")      .side(1)         # 5  [0]
    jmp("fetch")                .side(1)[1]      # 6  [1-2]
    label("rdb")
    out(pins, 8)                .side(0)[2]      # 7  [3-5] window-change bubble
    jmp("rd")                   .side(1)[2]      # 8  [0-2]
    label("hr_stall")
    jmp(y_dec, "hr_smp")        .side(1)         # 9  [1]  count this frame
    label("hr_smp")
    mov(osr, pins)              .side(1)         # 10 [2]  sample uo
    out(x, 4)                   .side(0)         # 11 [3]  keep the nibble
    out(null, 1)                .side(0)         # 12 [4]  drop write-ready
    out(pc, 1)                  .side(0)         # 13 [5]  read-valid -> 0 or 1
    label("wr")
    out(pins, 8)                .side(0)[2]      # 14 [3-5] drive ui (write-valid)
    label("hw_e")
    nop()                       .side(1)[1]      # 15 [0-1]
    jmp(pin, "fetch")           .side(1)         # 16 [2]  write-ready sampled high
    jmp(y_dec, "hw_h")          .side(0)         # 17 [3]  stall frame: count it
    label("hw_h")
    jmp("hw_e")                 .side(0)[1]      # 18 [4-5]
    label("halt")
    pull(block)                 .side(0)[1]      # 19 [3-4] clock stays low while stalled
    jmp("cyc0")                 .side(0)         # 20 [5]
    label("cyc")
    out(pins, 8)                .side(0)[2]      # 21 [3-5] drive ui
    label("cyc0")
    in_(y, 24)                  .side(1)[1]      # 22 [0-1]
    in_(pins, 8)                .side(1)         # 23 [2]  sample uo
    wrap_target()
    label("fetch")
    set(pins, 0)                .side(0)         # 24 [3]  write-valid, read-ready low
    push(iffull, noblock)       .side(0)         # 25 [4]  a complete result
    mov(x, status)              .side(0)         # 26 [5]  all-ones: TX FIFO empty
    jmp(not_x, "disp")          .side(1)         # 27 [0]
    jmp(y_dec, "fetch")         .side(1)[1]      # 28 [1-2] idle frame
    wrap()
    label("disp")
    pull()                      .side(1)         # 29 [1]
    out(pc, 5)                  .side(1)         # 30 [2]  -> handler at slot 3
    label("rd")
    out(pins, 8)                .side(0)[2]      # 31 [3-5] drive ui (read-ready); -> 0


def asm_options(pio_constants):
    """rp2.asm_pio keyword arguments; ``pio_constants`` is rp2.PIO (or the
    CPython stand-in with the same names)."""
    low = pio_constants.OUT_LOW
    right = pio_constants.SHIFT_RIGHT
    return {
        "out_init": (low,) * 8,
        "set_init": (low, low),
        "sideset_init": low,
        "in_shiftdir": right,
        "out_shiftdir": right,
        "autopush": False,
        "autopull": False,
        "push_thresh": 32,
        "pull_thresh": 32,
    }


def build_program(rp2):
    """Assemble the program with ``rp2.asm_pio`` and set STATUS_N. EXECCTRL is
    the fifth field from the end in every MicroPython release (index 3 up to
    v1.25, index 4 from v1.26, which added the PIO2 offset field)."""
    prog = rp2.asm_pio(**asm_options(rp2.PIO))(lockstep_program)
    prog[len(prog) - 5] |= EXECCTRL_STATUS
    return prog


def op_cycle(ui):
    return ADDR_CYC | (ui & 0xFF) << 5


def op_write(ui):
    return ADDR_WR | (ui & 0xFF) << 5


def op_read(ui):
    return ADDR_RD | (ui & 0xFF) << 5


def op_read_bubble(ui):
    """RD preceded by one frame that changes the window (the bubble)."""
    return ADDR_RDB | (ui & 0xFF) << 5 | (ui & 0xFF) << 13


OP_HALT = ADDR_HALT

# ---------------------------------------------------------------- pin maps
# Tiny Tapeout demo board v3 (RP2350B, SDK gpio_map_dbv3.py; docs/host.md
# "Wiring"): clock GPIO 16, ui_in GPIO 17..24, uo_out GPIO 33..40, all inside
# the PIO window GPIO 16..47 (GPIOBASE 16). rst_n (GPIO 14) is outside the
# window and is driven through the SDK while the clock is halted.
PIN_MAP_DB3 = {"clk": 16, "ui_in": tuple(range(17, 25)), "uo_out": tuple(range(33, 41))}


def check_pin_map(pin_map):
    """Raise HostError unless the map fits the program: 8 contiguous ui_in
    GPIOs, 6..8 contiguous uo_out GPIOs (write-ready and read-valid need
    uo[4] and uo[5]), and every GPIO inside one 32-GPIO PIO window."""
    ui = tuple(pin_map["ui_in"])
    uo = tuple(pin_map["uo_out"])
    if len(ui) != 8 or not 6 <= len(uo) <= 8:
        raise HostError("lockstep port needs 8 ui_in and 6..8 uo_out GPIOs")
    for run in (ui, uo):
        for i, gpio in enumerate(run):
            if gpio != run[0] + i:
                raise HostError("lockstep port needs contiguous ui_in and uo_out GPIOs "
                                "(OUT and IN pin groups); the RP2040 demo boards split ui_in")
    gpios = ui + uo + (pin_map["clk"],)
    low, high = min(gpios), max(gpios)
    base = 16 if low >= 16 else 0
    if high - base > 31:
        raise HostError("lockstep port needs clk, ui_in and uo_out inside one 32-GPIO "
                        "PIO window (GPIO 0..31, or 16..47 on the RP2350B)")
    return base


# ----------------------------------------------------------------- backend
class Rp2Backend:
    """The state machine on an RP2040/RP2350 under MicroPython.

    pin_map   dict with "clk", "ui_in", "uo_out" (see PIN_MAP_DB3 and the
              Pico maps in pe_host.ports.pico; other keys are ignored)
    reset     callable(active) that drives the project reset, or None
    ena       callable(level) for ena, or None (deselect() then refuses)
    sm_id     rp2.StateMachine id; default: the first state machine of the
              last PIO block (4 on the RP2040, 8 on the RP2350)
    modules   (rp2, machine, time[, array]) for tests; default: the MicroPython
              modules

    The program takes a whole PIO block (32 instructions); the backend removes
    every program from that block first.
    """

    def __init__(self, pin_map, reset=None, ena=None, sm_id=None, modules=None):
        if modules is None:
            import rp2
            import machine
            import time
            from array import array
            modules = (rp2, machine, time, array)
        self.rp2, self.machine, self.time = modules[0], modules[1], modules[2]
        if len(modules) > 3:
            self.array = modules[3]
        else:
            from array import array
            self.array = array
        self.pin_map = pin_map
        self.gpio_base = check_pin_map(pin_map)
        self._reset = reset
        self._ena = ena
        self.f_sys = self.machine.freq()
        self.frame_hz = self.f_sys // FRAME_CYCLES
        self.uo_visible = (1 << len(pin_map["uo_out"])) - 1
        if sm_id is None:
            sm_id = 8 if self._is_rp2350() else 4
        self.sm_id = sm_id
        self.prog = build_program(self.rp2)
        self._start()

    def _is_rp2350(self):
        try:
            return "RP2350" in self.machine_name()
        except Exception:
            return False

    def machine_name(self):
        import os
        return os.uname().machine

    def _start(self, first=True):
        rp2, machine = self.rp2, self.machine
        Pin = machine.Pin
        m = self.pin_map
        pio = rp2.PIO(self.sm_id // 4)
        if first:
            # The program needs the whole block (32 words at offset 0).
            pio.remove_program()
            if self.gpio_base:
                # RP2350B: GPIO 16..47 window (MicroPython v1.26+: PIO.gpio_base).
                pio.gpio_base(Pin(16))
            for gpio in m["uo_out"]:
                Pin(gpio, Pin.IN)      # input enabled (RP2350 pads start isolated)
        self.sm = rp2.StateMachine(self.sm_id, self.prog,
                                   sideset_base=Pin(m["clk"]), out_base=Pin(m["ui_in"][0]),
                                   set_base=Pin(m["ui_in"][4]), in_base=Pin(m["uo_out"][0]),
                                   jmp_pin=Pin(m["uo_out"][4]))
        self.sm.exec(INSTR_SET_Y_0)
        self.sm.exec(INSTR_JMP_FETCH)
        self.sm.active(1)

    # -- FIFO access -----------------------------------------------------------
    def put(self, word):
        self.sm.put(word)

    def put_many(self, words):
        """One sm.put of an array('I'): MicroPython writes it in one C loop,
        refilling the 4-word FIFO as the program drains it, so the words of a
        transaction follow each other without idle frames (a Python loop of
        single puts takes microseconds per word)."""
        self.sm.put(self.array("I", words))

    def wait_tx_space(self, words, timeout_us):
        """True once the TX FIFO has room for ``words`` words (4-deep FIFO);
        False after timeout_us microseconds."""
        sm = self.sm
        if sm.tx_fifo() + words <= 4:
            return True
        time = self.time
        start = time.ticks_us()
        while sm.tx_fifo() + words > 4:
            if time.ticks_diff(time.ticks_us(), start) > timeout_us:
                return False
        return True

    def get(self, timeout_us):
        """One RX word, or None after timeout_us microseconds."""
        sm = self.sm
        if not sm.rx_fifo():
            time = self.time
            start = time.ticks_us()
            while not sm.rx_fifo():
                if time.ticks_diff(time.ticks_us(), start) > timeout_us:
                    return None
        return sm.get()

    def wait_halted(self, timeout_us):
        """True once the program is stalled in HALT: it pulls HALT within a few
        PIO cycles after the TX FIFO drains, then stalls on the next pull.
        False if the FIFO did not drain within timeout_us microseconds."""
        sm = self.sm
        time = self.time
        start = time.ticks_us()
        while sm.tx_fifo():
            if time.ticks_diff(time.ticks_us(), start) > timeout_us:
                return False
        time.sleep_us(2)
        return True

    def restart(self):
        """Re-initialise the state machine (clears both FIFOs, clock low,
        ui_in = 0). The project clock pauses while this runs."""
        self.sm.active(0)
        self._start(first=False)

    def set_reset(self, active):
        if self._reset is None:
            raise HostError("no reset control for this board")
        self._reset(active)

    def set_ena(self, level):
        if self._ena is None:
            raise HostError("no ena control for this board")
        self._ena(level)

    @property
    def has_ena(self):
        return self._ena is not None

    def ticks_us(self):
        return self.time.ticks_us()

    def ticks_diff(self, a, b):
        return self.time.ticks_diff(a, b)

    def sleep_us(self, us):
        self.time.sleep_us(int(us))

    def release(self):
        """Stop the state machine with the clock low (a forced side-set 0)."""
        self.sm.active(0)
        self.sm.exec(INSTR_JMP_FETCH)


def demo_board_backend(tt=None, project="tt_um_teslacoilerow_protocol_emulator", sm_id=None,
                       modules=None):
    """Rp2Backend for the Tiny Tapeout demo board v3 through the ttboard SDK:
    selects the project, stops the SDK's clock and hands clk, ui_in and uo_out
    to the PIO; rst_n stays with the SDK (tt.reset_project)."""
    if tt is None:
        from ttboard.demoboard import DemoBoard
        tt = DemoBoard.get()
    from ttboard.mode import RPMode
    if tt.mode != RPMode.ASIC_RP_CONTROL:
        tt.mode = RPMode.ASIC_RP_CONTROL
    if project:
        if not tt.shuttle.has(project):
            raise HostError("project %s is not on this chip's shuttle" % project)
        tt.shuttle.get(project).enable()
    tt.clock_project_stop()
    manual = getattr(tt, "manual_project_clock", None)
    if manual is not None:
        manual.monitoring = False
    tt.uio_oe_pico.value = 0
    return Rp2Backend(PIN_MAP_DB3, reset=tt.reset_project, sm_id=sm_id, modules=modules)


# -------------------------------------------------------------------- port
class LockstepPort(Port):
    """pe_host port on a free-running PIO clock.

    ``cycle(ui)`` presents ui for exactly one project clock and returns uo
    sampled before its rising edge, like every port; between host operations
    the PIO keeps clocking with write-valid and read-ready low, which the
    protocol treats as idle cycles in the current window. ``count`` is the
    exact number of rising edges up to the last sampled one.

    In a cycle that changes the window, ready and valid are low by protocol
    (docs/isa.md: the window-change bubble) and the read nibble is undefined;
    the port reports them as 0 and samples only IRQ and FAULT, which do not
    depend on ui_in. Software peers (``env``) need host-supplied edges and are
    refused; attach real peripherals to uio.
    """

    name = "pio_lockstep"
    simulated = False
    RX_DEPTH = 4
    TX_DEPTH = 4
    WATCHDOG_US = 2000000

    def __init__(self, backend, watchdog_us=None):
        Port.__init__(self, None)
        self.backend = backend
        self.uo_visible = backend.uo_visible
        self.frame_hz = backend.frame_hz
        self.watchdog_us = self.WATCHDOG_US if watchdog_us is None else watchdog_us
        self.window = 0          # window bits currently on the ui pins
        self._sigma = 0          # frames of all dispatched operations (fetch + fixed)
        self._d = 0              # frames counted by Y (idle and stall frames)
        self._results = []       # [kind, sigma, put ticks, value, get ticks] in FIFO order
        self._outstanding = 0
        self._last_stamp = None  # (sigma, put ticks, get ticks) of the last stamp
        self.count_exact = True
        self.restarts = 0
        self.last_result = None

    # -- low level -------------------------------------------------------------
    def _check_env(self):
        if self.env is not None:
            raise HostError("the lockstep port runs a free clock: software peers need "
                            "host-supplied edges; attach real peripherals instead")

    def _account(self, word, kind):
        """Book one operation that is about to be put. kind: "stamp" (CYC,
        HALT), "data" (RD, RDB) or None (WR). Returns its result entry."""
        self._sigma += 1 + FIXED_FRAMES[word & 0x1F]
        if kind is None:
            return None
        self._outstanding += 1
        entry = [kind, self._sigma, self.backend.ticks_us(), None, None]
        self._results.append(entry)
        return entry

    def _dispatch(self, word, kind):
        """Put one operation."""
        return self._submit([(word, kind, False)])[0]

    def _submit(self, ops):
        """Put operations in chunks of at most TX_DEPTH words. ops: list of
        (word, kind, glue); glue=True keeps a word in the same chunk as the
        next one, so the pair reaches the FIFO in one write loop and the
        program runs them without idle frames between. A chunk is put only
        when the TX FIFO has room for all of it and at most RX_DEPTH results
        stay outstanding, so no put ever blocks: a program stuck in a
        handshake shows up as a watchdog timeout, not a hung CPU.
        Returns the result entries in order (None for WR)."""
        chunks = []
        chunk = []
        for i, op in enumerate(ops):
            chunk.append(op)
            if not op[2] or i == len(ops) - 1:
                # a boundary is allowed after this word
                if chunks and len(chunks[-1]) + len(chunk) <= self.TX_DEPTH:
                    chunks[-1].extend(chunk)
                else:
                    chunks.append(chunk)
                chunk = []
        entries = []
        for chunk in chunks:
            if len(chunk) > self.TX_DEPTH:
                raise HostError("glued operations exceed the TX FIFO")
            results = 0
            for op in chunk:
                if op[1] is not None:
                    results += 1
            while self._outstanding + results > self.RX_DEPTH:
                self._collect_one()
            if not self.backend.wait_tx_space(len(chunk), self.watchdog_us):
                self._timeout()
            words = []
            for word, kind, _ in chunk:
                entries.append(self._account(word, kind))
                words.append(word)
            if len(words) == 1:
                self.backend.put(words[0])
            else:
                self.backend.put_many(words)
        return entries

    def _timeout(self):
        self._recover()
        raise HostTimeout("lockstep port: nothing moved for %d us (a handshake that never "
                          "completed); the state machine was restarted" % self.watchdog_us)

    def _collect_one(self):
        entry = None
        for item in self._results:
            if item[3] is None:
                entry = item
                break
        if entry is None:
            raise HostError("no pending result")
        value = self.backend.get(self.watchdog_us)
        if value is None:
            self._timeout()
        self._outstanding -= 1
        entry[3] = value
        entry[4] = self.backend.ticks_us()
        if entry[0] == "stamp":
            self._stamp(entry)
        return entry

    def _wait(self, entry):
        while entry[3] is None:
            self._collect_one()
        self._results.remove(entry)
        return entry[3]

    def _stamp(self, entry):
        """count from a [uo | Y] result: rising edges up to the sampled one.

        Y holds 24 bits of the idle/stall frame count (2**24 frames: 0.67 s
        at 25 MHz). A wrap is resolved with the RP2's microsecond clock: the
        sampled edge lies between the operation's put and the result's get,
        which bounds the frames since the previous stamp.
        """
        _, sigma, put_t, value, get_t = entry
        self.last_result = value
        d24 = (-(value & Y_MASK)) & Y_MASK
        delta = (d24 - self._d) & Y_MASK
        if self._last_stamp is not None:
            last_sigma, last_put, last_get = self._last_stamp
            ticks_diff = self.backend.ticks_diff
            ops = sigma - last_sigma
            # ticks are whole microseconds: widen each bound by one tick
            low = (ticks_diff(put_t, last_get) - 1) * self.frame_hz // 1000000 - ops - 2
            high = (ticks_diff(get_t, last_put) + 1) * self.frame_hz // 1000000 - ops + 2
            if high - low >= 1 << Y_BITS:
                self.count_exact = False   # a result left unread for too long
            while delta < low:
                delta += 1 << Y_BITS
        self._d += delta
        self._last_stamp = (sigma, put_t, get_t)
        self.count = self._d + sigma

    def _recover(self):
        self.backend.restart()
        self.restarts += 1
        self.count_exact = False
        self._results = []
        self._outstanding = 0
        self.window = 0
        self._d = 0
        self._sigma = 0
        self._last_stamp = None

    def _sample(self, ui, window, value):
        uo = (value >> 24) & self.uo_visible
        if (ui >> 6) != window:
            uo &= 0xC0
        return uo

    # -- Port interface ----------------------------------------------------------
    def cycle(self, ui):
        self._check_env()
        window = self.window
        entry = self._dispatch(op_cycle(ui), "stamp")
        self.window = ui >> 6
        return self._sample(ui, window, self._wait(entry))

    def run_ops(self, ops):
        """Queue operations back to back and return their results in order:
        for "cyc" the uo sample (window-change cycles masked as in cycle()),
        for "rd" the 32-bit word; "wr" returns nothing. ops: list of
        (kind, ui). A "rd" that changes the window becomes RDB (bubble frame
        first); a "wr" must not change the window (its first sample would
        come too soon after the change)."""
        self._check_env()
        words = []
        meta = []
        window = self.window
        for kind, ui in ops:
            new = ui >> 6
            if kind == "cyc":
                words.append((op_cycle(ui), "stamp", False))
            elif kind == "rd":
                words.append((op_read(ui) if new == window else op_read_bubble(ui), "data", False))
            elif kind == "wr":
                if new != window:
                    raise HostError("wr operation must not change the window")
                words.append((op_write(ui), None, False))
            else:
                raise ValueError("unknown operation %r" % (kind,))
            meta.append((ui, window))
            window = new
        # keep each sample next to the transfer it brackets: a CYC before a WR
        # (the FAULT state before the word), the last WR before a CYC (after
        # it), an RD before its CYC
        for i in range(len(words) - 1):
            a, b = ops[i][0], ops[i + 1][0]
            if (a, b) in (("cyc", "wr"), ("wr", "cyc"), ("rd", "cyc")):
                words[i] = (words[i][0], words[i][1], True)
        self.window = window
        entries = self._submit(words)
        out = []
        for (ui, before), entry in zip(meta, entries):
            if entry is None:
                continue
            value = self._wait(entry)
            if entry[0] == "stamp":
                out.append(self._sample(ui, before, value))
            else:
                out.append(value)
        return out

    def _halt(self):
        entry = self._dispatch(OP_HALT, "stamp")
        if not self.backend.wait_halted(self.watchdog_us):
            self._timeout()
        return entry

    def _release(self, entry):
        self.backend.put(0)      # completes HALT's pull; not an operation
        self._wait(entry)

    def reset(self, cycles):
        """rst_n low for at least ``cycles`` rising edges with ui_in = 0; both
        reset transitions happen while the clock is halted low."""
        backend = self.backend
        entry = self._halt()
        backend.set_reset(True)
        self._release(entry)
        self.run_ops([("cyc", 0)] * max(1, cycles))
        entry = self._halt()
        backend.set_reset(False)
        self._release(entry)
        self.window = 0

    def deselect(self, cycles):
        backend = self.backend
        if not backend.has_ena:
            Port.deselect(self, cycles)
        entry = self._halt()
        backend.set_ena(0)
        self._release(entry)
        self.run_ops([("cyc", 0)] * max(1, cycles))
        entry = self._halt()
        backend.set_ena(1)
        self._release(entry)
        self.window = 0

    def wait_frames(self, frames, ui=None):
        """Let at least ``frames`` project clocks pass from now (the clock is
        free-running); returns the uo sample of the last cycle taken."""
        if ui is None:
            ui = self.window << 6
        uo = self.cycle(ui)
        end = self.count + frames
        while self.count < end:
            remaining = end - self.count
            if remaining > 8:
                self.backend.sleep_us(remaining * 1000000 // self.frame_hz)
            uo = self.cycle(ui)
        return uo

    def free_run(self, cycles, freq_hz=None):
        """The clock already runs at frame_hz; freq_hz is ignored."""
        self.wait_frames(cycles, 0)

    def release(self):
        """Stop the program; the project clock stops low."""
        self.backend.release()


# ---------------------------------------------------------------- emulator
class LockstepEmulator(ProtocolEmulator):
    """ProtocolEmulator whose word transfers run inside the PIO program.

    Behaviour is that of ProtocolEmulator on a free-running clock: every
    method gives the same results; only the number of idle cycles between host
    actions differs. Moved into the PIO:

    * write_word: one idle cycle, eight WR operations (each holds its nibble
      until write-ready), one idle cycle;
    * read_word in window 0, and in window 3 when levels() showed a word that
      only the host can remove: RD takes all eight nibbles.

    Everything else (partial transfers, pauses, try_* probes, reads of an RX
    queue of unknown level) uses ProtocolEmulator's per-cycle code through
    LockstepPort.cycle. A WR or RD that never completes (for example a TX
    write to a full queue of a halted engine) holds the PIO until the port's
    watchdog restarts the state machine and raises HostTimeout.
    """

    def __init__(self, port, **kw):
        ProtocolEmulator.__init__(self, port, **kw)
        self._rx_credit = {}
        self._route_sources = 0

    # -- bookkeeping for the RX fast path ---------------------------------------
    def _forget(self):
        ProtocolEmulator._forget(self)
        self._rx_credit = {}
        self._route_sources = 0

    def command(self, op, payload=0, check=None):
        ok = ProtocolEmulator.command(self, op, payload, check)
        if op == ROUTE and ok is not False:
            source = payload & 3
            if payload & 16 and (payload >> 5) & 0xFFFF:
                self._route_sources |= 1 << source
            self._rx_credit.pop(source, None)
        elif op == FLUSH and ok is not False:
            self._rx_credit.pop(self.selected, None)
        return ok

    def levels(self, engine=None):
        tx, rx = ProtocolEmulator.levels(self, engine)
        if not (self._route_sources >> self.selected) & 1:
            self._rx_credit[self.selected] = rx
        return tx, rx


    # -- word transfers -----------------------------------------------------------
    def write_word(self, window, word, timeout=None):
        if window not in (0, 1, 2) or not 0 <= word <= 0xFFFFFFFF:
            raise ValueError("invalid host write")
        if timeout is not None and timeout < 1:
            raise ValueError("timeout must be at least one cycle")
        base = window << 6
        ops = [("cyc", base)]
        for n in range(8):
            ops.append(("wr", base | UI_WVALID | ((word >> (4 * n)) & 15)))
        ops.append(("cyc", base))
        try:
            first, after = self.port.run_ops(ops)
        except HostTimeout:
            self.window = 0
            self.bounce()
            raise
        self.window = window
        self._first_uo = first
        self.uo = after
        return after

    def read_word(self, window=0, timeout=None, pauses=None):
        credit = self._rx_credit.get(self.selected, 0)
        fast = pauses is None and (window == 0 or (window == W_RX and credit > 0))
        if not fast:
            return ProtocolEmulator.read_word(self, window, timeout, pauses)
        if timeout is not None and timeout < 1:
            raise ValueError("timeout must be at least one cycle")
        base = window << 6
        try:
            word, uo = self.port.run_ops([("rd", base | UI_RREADY), ("cyc", base)])
        except HostTimeout:
            self.window = 0
            self.bounce()
            raise
        self.window = window
        if window == W_RX:
            self._rx_credit[self.selected] = credit - 1
        self.uo = uo
        return word

    # -- waiting on a free-running clock --------------------------------------------
    def idle(self, count=1):
        """At least ``count`` clocks with ui idle (one sample at the end)."""
        if count <= 1:
            return ProtocolEmulator.idle(self, count)
        self.uo = self.port.wait_frames(count, self.window << 6)
        return self.uo

    def run(self, cycles, watch_fault=True):
        """At least ``cycles`` clocks with ui idle; with watch_fault, raise
        EngineFault as soon as a sample shows uo[7] high."""
        port = self.port
        ui = self.window << 6
        self.uo = port.cycle(ui)
        end = port.count + cycles - 1
        while True:
            if watch_fault and self.uo & UO_FAULT:
                self.raise_on_fault()
            if port.count >= end:
                return self.uo
            self.uo = port.cycle(ui)

    def free_run(self, cycles, freq_hz=None):
        self.set_window(0)
        self.uo = self.port.wait_frames(cycles, 0)
        return self.uo

    # -- streaming helpers (never block the PIO) ---------------------------------------
    def rx_available(self, engine=None, limit=None):
        """Read the words the RX queue holds now (READ_SELECT 2 first)."""
        _, level = self.levels(engine)
        if limit is not None:
            level = min(level, limit)
        return [self.read_word(W_RX) for _ in range(level)]

    def tx_space(self, engine=None):
        tx, _ = self.levels(engine)
        return self.architecture["fifo_words"] - tx


def connect_lockstep(pin_map=None, reset=None, ena=None, emulator=True, **kw):
    """LockstepEmulator (or the bare port) on a Pico/Pico 2 pin map, or on the
    demo board v3 when pin_map is None."""
    if pin_map is None:
        backend = demo_board_backend()
    else:
        backend = Rp2Backend(pin_map, reset=reset, ena=ena)
    port = LockstepPort(backend)
    return LockstepEmulator(port, **kw) if emulator else port
