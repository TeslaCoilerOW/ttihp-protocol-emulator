# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Bring-up self-test: every host command, checked only through the host port.

Needs nothing attached to the chip. Section "trigger" makes one engine drive
``trigger_pin`` (default uio0) for a few cycles; everything else stays inside
the chip. Each section starts with a reset, so sections are independent. The
same code runs on the model, the cocotb RTL simulation, an FPGA behind a Pico
and the silicon on a demo board (MicroPython compatible).

Checks per section:
  identity         ISA version, post-reset status of every engine, exact timestamp rate
  fifo_loopback    echo program: prefill, abandoned TX nibbles, START, levels, held RX,
                   PC, completed-instruction count, abandoned RX read (no pop), IRQ
  backpressure     TX full holds write-ready low, FLUSH empties, FLUSH while running rejects
  route            autonomous RX->TX mover between two engines, word count exhaustion
  event            WAITEVENT stalls, EVENT delivers, mailbox consumed, TIME result
  trigger          TRIGGER rising-edge detector on a pin driven by another engine
  engine_fault     explicit FAULT code, fault report, FAULT pin, CLEAR
  host_fault       rejected commands set the sticky host fault; CLEAR bit 23 clears it
  program_abandon  partial program word abandoned; COMMIT length rules
  reset            reset invalidates images; START of an uncommitted engine rejects
"""

from .errors import CommandRejected, HostError
from .protocol import (RS_STATUS, SELECT, START, TRIG_RISING, TRIGGER, UO_FAULT, UO_IRQ,
                       UO_WREADY, W_PROGRAM, W_RX, W_TX)

# Opcodes (docs/isa.md) used by the self-test programs.
NOP, HALT, SET, DIR, WAIT, JMP, PULL, PUSH = 0, 1, 2, 3, 4, 5, 6, 7
WAITEVENT, MOV, TIME, FAULT = 15, 18, 28, 29
REG_TX, REG_RX = 0, 1


def ins(op, a=0, b=0, c=0):
    return (op << 24) | (a << 16) | (b << 8) | c


def imm(op, value=0):
    return (op << 24) | value


ECHO = [imm(PULL), ins(MOV, REG_RX, REG_TX), ins(PUSH), imm(JMP, 0)]
FAULTER = [imm(NOP), imm(FAULT, 0x55)]
WAITEV = [imm(WAITEVENT), ins(TIME, REG_RX), ins(PUSH), imm(HALT)]


def pulse(pin):
    mask = 1 << pin
    return [imm(SET, 0), imm(DIR, mask), imm(WAIT, 8), imm(SET, mask), imm(WAIT, 8),
            imm(SET, 0), imm(HALT)]


class SelfTestResult:
    def __init__(self):
        self.checks = []      # (section, name, ok, detail)
        self.errors = []      # (section, exception text)

    @property
    def passed(self):
        return not self.errors and all(c[2] for c in self.checks)

    @property
    def failures(self):
        return [c for c in self.checks if not c[2]]

    def summary(self):
        lines = ["self-test: %s (%d checks, %d failed, %d section errors)"
                 % ("PASS" if self.passed else "FAIL", len(self.checks), len(self.failures),
                    len(self.errors))]
        for section, name, ok, detail in self.checks:
            if not ok:
                lines.append("  FAIL %s/%s: %s" % (section, name, detail))
        for section, text in self.errors:
            lines.append("  ERROR %s: %s" % (section, text))
        return "\n".join(lines)


class SelfTest:
    def __init__(self, pe, trigger_pin=0, log=None):
        self.pe = pe
        self.trigger_pin = trigger_pin
        self.log = log
        self.result = SelfTestResult()
        self.section = ""

    def check(self, name, ok, detail=""):
        self.result.checks.append((self.section, name, bool(ok), detail))
        if self.log is not None:
            self.log("%s %s/%s %s" % ("ok  " if ok else "FAIL", self.section, name, detail))
        return ok

    def equal(self, name, got, want):
        return self.check(name, got == want, "got %r, expected %r" % (got, want))

    def run(self, sections=None):
        names = ("identity", "fifo_loopback", "backpressure", "route", "event", "trigger",
                 "engine_fault", "host_fault", "program_abandon", "reset")
        for name in names:
            if sections is not None and name not in sections:
                continue
            self.section = name
            try:
                self.pe.reset()
                getattr(self, "s_" + name)()
            except HostError as exc:
                self.result.errors.append((name, "%s: %s" % (type(exc).__name__, exc)))
                if self.log is not None:
                    self.log("ERROR %s: %s" % (name, exc))
        self.pe.reset()
        return self.result

    # ------------------------------------------------------------ sections
    def s_identity(self):
        pe = self.pe
        version = pe.isa_version()
        self.check("isa_version", version in pe.isa_versions, "READ_SELECT 7 = %d" % version)
        for engine in range(pe.engines):
            status = pe.status(engine)
            self.equal("status engine %d after reset" % engine, status.word, 0)
        pe.select(0)
        self.equal("FAULT/IRQ pins low", pe.uo & (UO_FAULT | UO_IRQ), 0)
        n1 = pe.cycles
        t1 = pe.timestamp()
        n2 = pe.cycles
        t2 = pe.timestamp()
        self.equal("timestamp advances once per host clock", (t2 - t1) & 0xFFFFFFFF, n2 - n1)

    def s_fifo_loopback(self):
        pe = self.pe
        pe.load_program(0, ECHO, verify=True)
        self.equal("levels after load", pe.levels(), (0, 0))
        self.check("abandoned TX nibbles", not pe.try_write_word(W_TX, 0xDEADBEEF, 4, nibbles=5))
        self.equal("abandoned TX write has no effect", pe.levels(), (0, 0))
        words = [0x12345678, 0x9ABCDEF0, 0x0F1E2D3C]
        pe.tx_write(words)
        self.equal("prefill before START", pe.levels(), (3, 0))
        pe.start(1)
        status = pe.status()
        self.check("running after START", status.running and status.committed, repr(status))
        pe.idle(16)
        self.equal("echo moved TX to RX", pe.levels(), (0, 3))
        self.check("IRQ while RX non-empty", pe.uo & UO_IRQ, "uo=0x%02x" % pe.uo)
        self.equal("held RX = last echoed word", pe.held_rx(), words[-1])
        status = pe.status()
        self.check("stalled on PULL with TX empty", status.stalled and status.running, repr(status))
        self.equal("PC parked on PULL", pe.pc(), 0)
        self.equal("completed instructions", pe.completed(), 4 * len(words))
        partial = pe.try_read_word(W_RX, 8, nibbles=3)
        self.check("abandoned RX read returns None", partial is None)
        self.equal("abandoned RX read does not pop", pe.levels(), (0, 3))
        got = pe.rx_read_many(3)
        self.equal("RX words", got, words)
        self.equal("RX empty", pe.rx_try_read(8), None)
        pe.idle(2)
        self.equal("IRQ low when drained", pe.uo & UO_IRQ, 0)
        pe.stop(1)
        self.check("halted after STOP", not pe.status().running)

    def s_backpressure(self):
        pe = self.pe
        depth = pe.architecture["fifo_words"]
        pe.load_program(1, ECHO)
        pe.tx_write(list(range(1, depth + 1)))
        self.equal("TX full", pe.levels(), (depth, 0))
        self.check("write-ready low while TX full",
                   not pe.try_write_word(W_TX, 0x55, 16))
        pe.set_window(W_TX)
        pe.idle(1)
        self.equal("write-ready sampled low", pe.uo & UO_WREADY, 0)
        pe.flush()
        self.equal("FLUSH empties", pe.levels(), (0, 0))
        pe.start(2)
        try:
            pe.flush()
            self.check("FLUSH while running rejected", False, "accepted")
        except CommandRejected:
            self.check("FLUSH while running rejected", True)
        pe.stop(2)
        pe.clear(0, host_fault=True)
        self.equal("host fault cleared", pe.uo & UO_FAULT, 0)

    def s_route(self):
        pe = self.pe
        pe.load_program(0, ECHO)
        pe.load_program(1, ECHO)
        pe.route(0, 1, 2)
        pe.start(3)
        pe.tx_write([0xA1, 0xB2, 0xC3], engine=0)
        pe.idle(24)
        self.equal("routed words arrive in engine 1 RX", pe.levels(1), (0, 2))
        self.equal("count exhausted: third word stays in engine 0 RX", pe.levels(0), (0, 1))
        self.equal("engine 1 RX words", pe.rx_read_many(2, engine=1), [0xA1, 0xB2])
        self.equal("engine 0 RX word", pe.rx_read(engine=0), 0xC3)
        pe.route(0, 1, 5)
        pe.unroute(0)
        pe.tx_write(0xD4, engine=0)
        pe.idle(16)
        self.equal("disabled route leaves the word in engine 0", pe.levels(0), (0, 1))
        pe.stop()

    def s_event(self):
        pe = self.pe
        pe.load_program(2, WAITEV)
        pe.start(4)
        pe.idle(4)
        status = pe.status(2)
        self.check("WAITEVENT stalls", status.running and status.stalled, repr(status))
        self.equal("no mailbox pending", pe.event_pending(), False)
        pe.event(4)
        pe.idle(8)
        status = pe.status()
        self.check("halted after HALT", not status.running and not status.faulted, repr(status))
        self.equal("mailbox consumed", pe.event_pending(), False)
        stamp = pe.rx_read()
        self.check("TIME result pushed", 0 < stamp < pe.cycles, "timestamp %d" % stamp)
        pe.event(4)
        pe.idle(2)
        self.equal("EVENT to a halted engine stays pending", pe.event_pending(2), True)

    def s_trigger(self):
        pe = self.pe
        pin = self.trigger_pin
        pe.load_program(3, pulse(pin), owned=1 << pin)
        pe.load_program(2, [imm(HALT)])
        pe.trigger(pin, TRIG_RISING, engine=2)
        pe.idle(4)
        self.equal("static level makes no edge", pe.event_pending(2), False)
        pe.start(8)
        pe.idle(40)
        self.equal("rising edge on uio%d delivers the mailbox" % pin, pe.event_pending(2), True)
        self.check("IRQ follows the mailbox", pe.uo & UO_IRQ, "uo=0x%02x" % pe.uo)
        pe.disable_trigger(engine=2)
        self.equal("pulse engine halted", pe.status(3).running, False)

    def s_engine_fault(self):
        pe = self.pe
        pe.load_program(3, FAULTER)
        pe.start(8)
        pe.idle(4)
        status = pe.status(3)
        self.check("explicit FAULT code", status.faulted and status.fault == 0x55, repr(status))
        self.check("FAULT pin high", pe.uo & UO_FAULT)
        report = pe.fault_report()
        self.check("fault report names engine 3",
                   [s.engine for s in report.faulted] == [3] and report.host_fault is None,
                   repr(report))
        ok = pe.command(START, 8, check=False)
        self.equal("START of a faulted engine: acceptance unknown under FAULT", ok, None)
        self.check("still faulted", pe.status(3).fault == 0x55)
        pe.clear_faults(host_fault=True)
        self.equal("CLEAR removes the fault", pe.status(3).word & 0xFF08, 0)
        pe.idle(1)
        self.equal("FAULT pin low", pe.uo & UO_FAULT, 0)

    def s_host_fault(self):
        pe = self.pe
        cases = (("SELECT engine out of range", SELECT, pe.engines),
                 ("READ_SELECT index 8", 8, 8),
                 ("TRIGGER reserved bit", TRIGGER, 1 << 6),
                 ("START uncommitted engine", START, 1),
                 ("unknown command 12", 12, 0))
        for name, op, payload in cases:
            try:
                pe.command(op, payload)
                self.check(name + " rejected", False, "accepted")
            except CommandRejected:
                report = pe.fault_report()
                self.check(name + " rejected", report.host_fault is True, repr(report))
            pe.clear(0, host_fault=True)
            self.equal(name + ": host fault cleared", pe.uo & UO_FAULT, 0)
        pe.load_program(0, [imm(HALT)], owned=0x03)
        pe.select(1)
        pe.begin()
        try:
            pe.own(0x02)
            self.check("overlapping OWN rejected", False, "accepted")
        except CommandRejected:
            self.check("overlapping OWN rejected", True)
        pe.clear(0, host_fault=True)
        try:
            pe.own(0x10, open_drain=0x20)
            self.check("open-drain outside ownership rejected", False, "accepted")
        except CommandRejected:
            self.check("open-drain outside ownership rejected", True)
        pe.clear(0, host_fault=True)
        self.equal("host fault cleared", pe.uo & UO_FAULT, 0)

    def s_program_abandon(self):
        pe = self.pe
        pe.select(1)
        pe.begin()
        pe.write_word(W_PROGRAM, ECHO[0])
        pe.write_word(W_PROGRAM, ECHO[1])
        self.check("partial program word abandoned",
                   not pe.try_write_word(W_PROGRAM, ECHO[2], 4, nibbles=6))
        try:
            pe.commit(3)
            self.check("COMMIT with a length never written rejected", False, "accepted")
        except CommandRejected:
            self.check("COMMIT with a length never written rejected", True)
        pe.clear(0, host_fault=True)
        pe.write_word(W_PROGRAM, ECHO[2])
        pe.write_word(W_PROGRAM, ECHO[3])
        pe.commit(4)
        status = pe.status()
        self.check("committed after the abandoned word", status.committed and not status.running,
                   repr(status))
        pe.tx_write(0x77)
        pe.start(2)
        pe.idle(8)
        self.equal("image runs as written", pe.rx_read(), 0x77)
        pe.stop(2)
        pe.set_window(W_PROGRAM)
        pe.idle(1)
        self.equal("write-ready low in window 1 after COMMIT", pe.uo & UO_WREADY, 0)

    def s_reset(self):
        pe = self.pe
        pe.load_program(0, ECHO)
        self.check("committed before reset", pe.status(0).committed)
        pe.reset()
        self.equal("reset invalidates the image", pe.status(0).word, 0)
        try:
            pe.start(1)
            self.check("START after reset rejected", False, "accepted")
        except CommandRejected:
            self.check("START after reset rejected", True)
        pe.clear(0, host_fault=True)


def run(pe, trigger_pin=0, log=None, sections=None):
    """Run the self-test; returns SelfTestResult (result.passed, result.summary())."""
    return SelfTest(pe, trigger_pin, log).run(sections)
