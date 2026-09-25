# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""ProtocolEmulator: the one host API for every backend (docs/host.md).

The host port is synchronous (docs/isa.md, "Host interface"), so the host
supplies every clock edge. A backend ("port") implements one primitive,
``port.cycle(ui)``: drive ui_in, sample uo_out before the rising edge, then
apply that edge. Everything else here is portable MicroPython/CPython code.

Cycle timing deliberately equals the cocotb harness (test/harness.py) so a
host-library run and a harness run of the same operations are cycle-identical
(host/tests/test_host_harness_equivalence.py checks this):

* a window change costs one bubble cycle with valid/ready low;
* a word write holds write-valid per nibble until write-ready was sampled high,
  then spends one idle cycle (whose sample shows the post-word FAULT state);
* a word read holds read-ready until read-valid was sampled high, per nibble;
* a READ_SELECT read is: command, one cycle in window 1 (drops a stale
  snapshot), then a window-0 read.
"""

from .errors import CommandRejected, EngineFault, HostError, HostTimeout, IsaMismatch
from .image import FirmwareImage
from .protocol import (BEGIN, CLEAR, CLEAR_HOST_FAULT, COMMAND_NAMES, COMMIT, DESIGN_ARCHITECTURE,
                       EVENT, FLUSH, OWN, READ_SELECT, ROUTE, RS_COUNT, RS_EVENT, RS_HELD_RX,
                       RS_LEVELS, RS_PC, RS_STATUS, RS_TIMESTAMP, RS_VERSION, SELECT, START, STOP,
                       TRIGGER, UI_RREADY, UI_WVALID, UO_FAULT, UO_IRQ, UO_RVALID, UO_WREADY,
                       W_COMMAND, W_PROGRAM, W_RX, W_TX, Status, command_word, fault_name,
                       own_payload, route_payload, split_levels, trigger_payload)


class FaultReport:
    """Snapshot of every engine's status plus the inferred sticky host fault."""

    def __init__(self, statuses, fault_pin):
        self.statuses = statuses            # list of Status, index = engine
        self.fault_pin = fault_pin          # uo[7] sampled after the reads (None: not wired)
        self.faulted = [s for s in statuses if s.faulted]
        if fault_pin is None:
            self.host_fault = None          # uo[7] not visible on this port: unknown
        elif not fault_pin:
            self.host_fault = False
        elif not self.faulted:
            self.host_fault = True
        else:
            self.host_fault = None          # masked by an engine fault: unknown

    @property
    def ok(self):
        """No engine faulted and uo[7] low. On a port without uo[7] only the
        engines are checked (host_fault is then None: unknown)."""
        return not self.fault_pin and not self.faulted

    def __repr__(self):
        if self.ok and self.fault_pin is None:
            return "<FaultReport no engine faults; host fault unknown (uo[7] not wired to this host)>"
        if self.ok:
            return "<FaultReport ok>"
        parts = ["engine %d: fault %d (%s)" % (s.engine, s.fault, s.fault_name)
                 for s in self.faulted]
        if self.host_fault:
            parts.append("sticky host fault (a rejected command)")
        elif self.host_fault is None and self.fault_pin is None:
            parts.append("host fault unknown (uo[7] not wired to this host)")
        elif self.host_fault is None:
            parts.append("host fault unknown (masked by engine faults)")
        return "<FaultReport " + "; ".join(parts) + ">"


class ProtocolEmulator:
    """Drive tt_um_teslacoilerow_protocol_emulator through its nibble host port.

    port:         a backend from pe_host.ports (model, ttboard, pico, cocotb, replay)
    timeout:      default cycle budget for one blocked nibble handshake
    architecture: the device architecture images must be bound to
    isa_versions: READ_SELECT 7 values accepted by check_isa()
    strict:       raise CommandRejected when uo[7] rises across a command word
    """

    def __init__(self, port, timeout=100000, architecture=None, isa_versions=(2,),
                 strict=True, log=None):
        self.port = port
        self.timeout = timeout
        self.architecture = DESIGN_ARCHITECTURE if architecture is None else architecture
        self.engines = self.architecture["engine_count"]
        self.all_engines = (1 << self.engines) - 1
        self.isa_versions = tuple(isa_versions)
        self.strict = strict
        self.log = log
        self.uo = 0
        self.window = 0
        self.selected = 0
        self.read_selected = 0
        self.device_isa = None
        self._first_uo = 0
        self.last_command_ok = None

    # ------------------------------------------------------------ primitives
    @property
    def cycles(self):
        """Clock edges applied so far by this port (including reset cycles)."""
        return self.port.count

    @property
    def irq(self):
        return bool(self.uo & UO_IRQ)

    @property
    def fault_pin(self):
        return bool(self.uo & UO_FAULT)

    @property
    def fault_visible(self):
        """False when the port does not see uo[7] (e.g. the Urbana FPGA build,
        which brings out only uo_out[5:0]); command acceptance is then known
        only for SELECT and READ_SELECT (see command())."""
        return bool(self.port.uo_visible & UO_FAULT)

    def _static_acceptance(self, op, payload):
        """Acceptance of commands whose validity depends only on the payload
        (docs/isa.md; test/model/reference.py command()): SELECT accepts an
        engine number below engine_count, READ_SELECT an index 0..7. None for
        every other (state-dependent) command."""
        if op == SELECT:
            return payload < self.engines
        if op == READ_SELECT:
            return payload <= RS_VERSION
        return None

    def cycle(self, ui=None):
        """One clock: drive ui (default: current window, no valid/ready), return uo."""
        if ui is None:
            ui = self.window << 6
        self.uo = self.port.cycle(ui)
        return self.uo

    def idle(self, count=1):
        ui = self.window << 6
        cycle = self.port.cycle
        for _ in range(count):
            self.uo = cycle(ui)
        return self.uo

    def reset(self, cycles=4):
        """Hold rst_n low for ``cycles`` edges. Invalidates every image and queue."""
        self.port.reset(cycles)
        self._forget()

    def deselect(self, cycles=2):
        """Hold ena low (model/cocotb backends only); same effect as reset."""
        self.port.deselect(cycles)
        self._forget()

    def _forget(self):
        self.window = 0
        self.selected = 0
        self.read_selected = 0
        self.uo = 0
        self.port.forbidden = 0

    def set_window(self, window):
        """Change the window; the change costs one bubble cycle."""
        if self.window != window:
            self.window = window
            self.uo = self.port.cycle(window << 6)

    def bounce(self):
        """Leave the current window for one cycle: abandons any partial transfer."""
        self.set_window(self.window ^ 1)

    def write_word(self, window, word, timeout=None):
        """Write one 32-bit word as eight little-endian nibbles; returns the uo
        sample of the idle cycle after the last accepted nibble."""
        if window not in (0, 1, 2) or not 0 <= word <= 0xFFFFFFFF:
            raise ValueError("invalid host write")
        if timeout is None:
            timeout = self.timeout
        if timeout < 1:
            raise ValueError("timeout must be at least one cycle")
        cycle = self.port.cycle
        first = None
        if self.window != window:
            self.window = window
            first = cycle(window << 6)
        base = (window << 6) | UI_WVALID
        for n in range(8):
            ui = base | ((word >> (4 * n)) & 15)
            for _ in range(timeout):
                uo = cycle(ui)
                if first is None:
                    first = uo
                if uo & UO_WREADY:
                    break
            else:
                self.uo = uo
                self.bounce()
                raise HostTimeout("write-ready stayed low for %d cycles (window %d, nibble %d)"
                                  % (timeout, window, n))
        self._first_uo = first
        self.uo = cycle(window << 6)
        return self.uo

    def read_word(self, window=0, timeout=None, pauses=None):
        """Read one word: window 0 = selected status word, 3 = selected RX FIFO.

        pauses: optional per-nibble idle cycles inserted before each nibble
        (read-ready low), to exercise arbitrary read stalls.
        """
        if window not in (0, 3):
            raise ValueError("invalid host read")
        if timeout is None:
            timeout = self.timeout
        if timeout < 1:
            raise ValueError("timeout must be at least one cycle")
        self.set_window(window)
        cycle = self.port.cycle
        ui = (window << 6) | UI_RREADY
        idle_ui = window << 6
        result = 0
        for n in range(8):
            if pauses is not None and n < len(pauses):
                for _ in range(pauses[n]):
                    self.uo = cycle(idle_ui)
            for _ in range(timeout):
                uo = cycle(ui)
                if uo & UO_RVALID:
                    result |= (uo & 15) << (4 * n)
                    break
            else:
                self.uo = uo
                self.bounce()
                raise HostTimeout("read-valid stayed low for %d cycles (window %d, nibble %d)"
                                  % (timeout, window, n))
        self.uo = uo
        return result

    def try_write_word(self, window, word, max_wait, nibbles=8):
        """Like write_word, but give up (window bounce) after ``max_wait`` stalled
        cycles on a nibble, or after ``nibbles`` < 8 accepted nibbles (deliberate
        abandon). Returns True only when all eight nibbles were accepted."""
        self.set_window(window)
        cycle = self.port.cycle
        base = (window << 6) | UI_WVALID
        for n in range(nibbles):
            ui = base | ((word >> (4 * n)) & 15)
            for _ in range(max_wait + 1):
                self.uo = cycle(ui)
                if self.uo & UO_WREADY:
                    break
            else:
                self.bounce()
                return False
        if nibbles < 8:
            self.bounce()
            return False
        self.uo = cycle(window << 6)
        return True

    def try_read_word(self, window, max_wait, nibbles=8, pauses=None):
        """Like read_word, but abandon (window bounce) on timeout or after
        ``nibbles`` < 8; returns None when abandoned. An abandoned RX read does
        not pop the word (docs/isa.md: pop only at the last accepted nibble)."""
        self.set_window(window)
        cycle = self.port.cycle
        ui = (window << 6) | UI_RREADY
        idle_ui = window << 6
        result = 0
        for n in range(nibbles):
            if pauses is not None and n < len(pauses):
                for _ in range(pauses[n]):
                    self.uo = cycle(idle_ui)
            for _ in range(max_wait + 1):
                self.uo = cycle(ui)
                if self.uo & UO_RVALID:
                    result |= (self.uo & 15) << (4 * n)
                    break
            else:
                self.bounce()
                return None
        if nibbles < 8:
            self.bounce()
            return None
        return result

    # -------------------------------------------------------------- commands
    def command(self, op, payload=0, check=None):
        """Write one command word in window 0.

        Returns True when uo[7] (FAULT) is low after the word, False when it
        rose across the word (the chip rejected it and set its sticky host
        fault) and None when it was already high (acceptance unknown).
        SELECT and READ_SELECT are the exception: their acceptance depends
        only on the payload (docs/isa.md), so they return that exact answer
        even under a high FAULT pin, when an engine fault rises during the
        word, or on a port that cannot see uo[7]. The library's record of
        the selections follows that answer, so it never diverges from the
        chip. Other commands return None on a port without uo[7].
        check (default self.strict): raise CommandRejected on False.
        """
        word = command_word(op, payload)
        after = self.write_word(W_COMMAND, word)
        known = self._static_acceptance(op, payload)
        if known is not None or not self.port.uo_visible & UO_FAULT:
            ok = known
        elif after & UO_FAULT:
            ok = None if self._first_uo & UO_FAULT else False
        else:
            ok = True
        self.last_command_ok = ok
        if self.log is not None:
            name = COMMAND_NAMES[op] if op < len(COMMAND_NAMES) else "OP%d" % op
            self.log("%s 0x%06x -> %s" % (name, payload,
                                          {True: "ok", False: "REJECTED", None: "?"}[ok]))
        if ok is False:
            if check is None:
                check = self.strict
            if check:
                name = COMMAND_NAMES[op] if op < len(COMMAND_NAMES) else "op %d" % op
                raise CommandRejected("%s 0x%06x rejected (FAULT pin rose); "
                                      "clear with clear(host_fault=True)" % (name, payload),
                                      op, payload)
            return ok
        if op == SELECT:
            self.selected = payload
        elif op == READ_SELECT:
            self.read_selected = payload
        elif op == OWN:
            self.port.forbidden |= (payload & 0xFF) & ~((payload >> 8) & 0xFF)
        return ok

    def _mask(self, mask):
        if not 0 <= mask <= self.all_engines:
            raise ValueError("engine mask 0x%x outside %d engines" % (mask, self.engines))
        return mask

    def _engine(self, engine):
        if not 0 <= engine < self.engines:
            raise ValueError("engine %d outside %d engines" % (engine, self.engines))
        return engine

    def select(self, engine):
        return self.command(SELECT, self._engine(engine))

    def use(self, engine):
        """SELECT only if ``engine`` is not already selected (None = keep)."""
        if engine is not None and engine != self.selected:
            self.select(engine)

    def begin(self, engine=None):
        self.use(engine)
        return self.command(BEGIN)

    def commit(self, length):
        if not 0 < length <= 0xFFFF:
            raise ValueError("COMMIT length")
        return self.command(COMMIT, length)

    def own(self, pins, open_drain=0, engine=None):
        self.use(engine)
        return self.command(OWN, own_payload(pins, open_drain))

    def start(self, mask):
        return self.command(START, self._mask(mask))

    def stop(self, mask=None):
        return self.command(STOP, self.all_engines if mask is None else self._mask(mask))

    def route(self, source, destination, count, enable=True):
        self._engine(source)
        self._engine(destination)
        return self.command(ROUTE, route_payload(source, destination, count, enable))

    def unroute(self, source):
        return self.command(ROUTE, route_payload(self._engine(source), 0, 0, False))

    def clear(self, mask=0, host_fault=False):
        """CLEAR engine faults in mask (engines must be halted); host_fault=True
        also clears the sticky host fault (payload bit 23)."""
        payload = self._mask(mask) | (CLEAR_HOST_FAULT if host_fault else 0)
        return self.command(CLEAR, payload)

    def event(self, mask):
        return self.command(EVENT, self._mask(mask))

    def flush(self, engine=None):
        self.use(engine)
        return self.command(FLUSH)

    def trigger(self, pin, mode, enable=True, engine=None):
        self.use(engine)
        return self.command(TRIGGER, trigger_payload(pin, mode, enable))

    def disable_trigger(self, engine=None):
        self.use(engine)
        return self.command(TRIGGER, 0)

    # ----------------------------------------------------------------- reads
    def read_status(self, index, timeout=None):
        """READ_SELECT index, one cycle in window 1 to drop a stale snapshot,
        then read the fresh word in window 0 (raw 32-bit value)."""
        self.command(READ_SELECT, index)
        self.window = 1
        self.uo = self.port.cycle(1 << 6)
        return self.read_word(W_COMMAND, timeout)

    def status(self, engine=None):
        self.use(engine)
        return Status(self.read_status(RS_STATUS), self.selected)

    def timestamp(self):
        return self.read_status(RS_TIMESTAMP)

    def levels(self, engine=None):
        """(tx_level, rx_level) of the selected engine's queues."""
        self.use(engine)
        return split_levels(self.read_status(RS_LEVELS))

    def pc(self, engine=None):
        self.use(engine)
        return self.read_status(RS_PC)

    def event_pending(self, engine=None):
        self.use(engine)
        return bool(self.read_status(RS_EVENT) & 1)

    def completed(self, engine=None):
        self.use(engine)
        return self.read_status(RS_COUNT)

    def held_rx(self, engine=None):
        self.use(engine)
        return self.read_status(RS_HELD_RX)

    def isa_version(self):
        self.device_isa = self.read_status(RS_VERSION)
        return self.device_isa

    def check_isa(self):
        version = self.isa_version()
        if version not in self.isa_versions:
            raise IsaMismatch("device reports ISA version %d; accepted: %r"
                              % (version, self.isa_versions))
        return version

    # --------------------------------------------------------------- loading
    def load_program(self, engine, words, owned=0, open_drain=0, verify=False):
        """SELECT, BEGIN, OWN, program words (window 1), COMMIT.

        The engine is stopped by BEGIN. verify=True reads the status back and
        requires committed=1, running=0.
        """
        if not 0 < len(words) <= self.architecture["program_words"]:
            raise ValueError("program length %d outside 1..%d"
                             % (len(words), self.architecture["program_words"]))
        self.select(engine)
        self.command(BEGIN)
        self.command(OWN, own_payload(owned, open_drain))
        for word in words:
            self.write_word(W_PROGRAM, word)
        self.command(COMMIT, len(words))
        if verify:
            status = self.status()
            if not status.committed or status.running:
                raise HostError("engine %d image not committed: %r" % (engine, status))
        return len(words)

    def load_image(self, image, engine=None, check_isa=True, verify=False):
        """Load a verified FirmwareImage (or a path to one).

        Checks the architecture binding against self.architecture and, with
        check_isa, reads READ_SELECT 7 and requires an accepted version that is
        at least the image's isa_version. engine defaults to image.engine.
        """
        if not isinstance(image, FirmwareImage):
            image = FirmwareImage.load(image)
        image.check_binding(self.architecture)
        if check_isa:
            version = self.isa_version()
            if version not in self.isa_versions:
                raise IsaMismatch("device reports ISA version %d; accepted: %r"
                                  % (version, self.isa_versions))
            image.check_isa(version)
        target = image.engine if engine is None else engine
        self.load_program(target, image.words, image.owned_pins, image.open_drain, verify)
        return image

    # ---------------------------------------------------------------- queues
    def tx_write(self, words, engine=None, timeout=None):
        """Append word(s) to the selected (or given) engine's TX FIFO.

        Blocks under backpressure (write-ready low while the FIFO is full) for
        at most ``timeout`` cycles per nibble, then raises HostTimeout.
        """
        self.use(engine)
        if isinstance(words, int):
            words = (words,)
        for word in words:
            self.write_word(W_TX, word, timeout)
        return len(words)

    def tx_try_write(self, word, max_wait=0, engine=None):
        self.use(engine)
        return self.try_write_word(W_TX, word, max_wait)

    def rx_read(self, engine=None, timeout=None):
        """Pop one word from the selected (or given) engine's RX FIFO."""
        self.use(engine)
        return self.read_word(W_RX, timeout)

    def rx_read_many(self, count, engine=None, timeout=None):
        self.use(engine)
        return [self.read_word(W_RX, timeout) for _ in range(count)]

    def rx_try_read(self, max_wait=4, engine=None):
        """One RX word, or None when none became valid within max_wait cycles."""
        self.use(engine)
        return self.try_read_word(W_RX, max_wait)

    def rx_drain(self, engine=None, limit=1 << 16):
        """Read every word currently in the RX FIFO (level from READ_SELECT 2)."""
        self.use(engine)
        out = []
        while len(out) < limit:
            _, level = self.levels()
            if level == 0:
                break
            for _ in range(min(level, limit - len(out))):
                out.append(self.read_word(W_RX))
        return out

    # ---------------------------------------------------------------- faults
    def fault_report(self):
        """Read every engine's status (restoring the selection) and infer the
        sticky host fault from uo[7]."""
        previous = self.selected
        statuses = []
        for engine in range(self.engines):
            self.select(engine)
            statuses.append(Status(self.read_status(RS_STATUS), engine))
        if previous != self.selected:
            self.select(previous)
        return FaultReport(statuses, bool(self.uo & UO_FAULT) if self.fault_visible else None)

    def clear_faults(self, mask=None, host_fault=True):
        """CLEAR faulted engines and the host fault. mask=None reads a
        fault_report() first and clears exactly the faulted engines (a mask that
        includes a running engine would make the whole CLEAR reject)."""
        if mask is None:
            mask = 0
            for status in self.fault_report().faulted:
                mask |= 1 << status.engine
        return self.clear(mask, host_fault)

    def raise_on_fault(self):
        if self.uo & UO_FAULT:
            report = self.fault_report()
            raise EngineFault("FAULT pin high: %r" % (report,), report)

    # --------------------------------------------------------------- waiting
    def wait_until(self, predicate, timeout, what="condition"):
        """Idle one cycle at a time until predicate() is true; returns cycles waited."""
        for n in range(timeout):
            if predicate():
                return n
            self.idle(1)
        raise HostTimeout("timeout after %d cycles waiting for %s" % (timeout, what))

    def wait_irq(self, timeout=None):
        """Idle until uo[6] (IRQ: a mailbox pending or an RX FIFO non-empty)."""
        if not self.port.uo_visible & UO_IRQ:
            raise HostError("this port does not see uo[6] (IRQ); poll levels() instead")
        if timeout is None:
            timeout = self.timeout
        for n in range(timeout):
            if self.idle(1) & UO_IRQ:
                return n
        raise HostTimeout("IRQ stayed low for %d cycles" % timeout)

    def run(self, cycles, watch_fault=True):
        """Idle ``cycles`` clocks; with watch_fault raise EngineFault as soon as
        uo[7] is sampled high."""
        ui = self.window << 6
        cycle = self.port.cycle
        for _ in range(cycles):
            self.uo = cycle(ui)
            if watch_fault and self.uo & UO_FAULT:
                self.raise_on_fault()
        return self.uo

    def free_run(self, cycles, freq_hz=None):
        """Let the engines run for ``cycles`` clocks with ui held idle.

        Hardware ports with freq_hz use their free-running clock (PWM) for
        cycles/freq_hz seconds (approximate edge count); otherwise the host
        supplies each edge.
        """
        self.set_window(0)
        if freq_hz:
            self.port.free_run(cycles, freq_hz)
        else:
            self.idle(cycles)
        return self.uo


def describe_status(status):
    """One-line description used by examples."""
    text = repr(status)
    if status.faulted:
        text += " -> " + fault_name(status.fault)
    return text
