# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Host-port constants and word encoders for ISA v2 (docs/isa.md, "Host interface").

MicroPython compatible: no dataclasses, enums, typing or f-string features
beyond plain substitution. Every number here is taken from docs/isa.md.
"""

# ui_in bits (driven by the host)
UI_NIBBLE = 0x0F
UI_WVALID = 0x10
UI_RREADY = 0x20
UI_WINDOW_SHIFT = 6

# uo_out bits (sampled by the host before the rising edge)
UO_NIBBLE = 0x0F
UO_WREADY = 0x10
UO_RVALID = 0x20
UO_IRQ = 0x40
UO_FAULT = 0x80

# Windows
W_COMMAND = 0   # write: command word; read: status word chosen by READ_SELECT
W_PROGRAM = 1   # write: next program word of the selected engine
W_TX = 2        # write: TX FIFO word of the selected engine
W_RX = 3        # read: RX FIFO word of the selected engine (popped at nibble 8)

# Window 0 commands (op in bits 31:24, payload in bits 23:0)
SELECT = 0
BEGIN = 1
COMMIT = 2
OWN = 3
START = 4
STOP = 5
ROUTE = 6
CLEAR = 7
READ_SELECT = 8
EVENT = 9
FLUSH = 10
TRIGGER = 11

COMMAND_NAMES = ("SELECT", "BEGIN", "COMMIT", "OWN", "START", "STOP", "ROUTE",
                 "CLEAR", "READ_SELECT", "EVENT", "FLUSH", "TRIGGER")

# READ_SELECT indices
RS_STATUS = 0
RS_TIMESTAMP = 1
RS_LEVELS = 2
RS_PC = 3
RS_EVENT = 4
RS_COUNT = 5
RS_HELD_RX = 6
RS_VERSION = 7

READ_SELECT_NAMES = ("status", "timestamp", "levels", "pc", "event", "completed",
                     "held_rx", "isa_version")

# TRIGGER modes
TRIG_RISING = 0
TRIG_FALLING = 1
TRIG_HIGH = 2
TRIG_LOW = 3

CLEAR_HOST_FAULT = 1 << 23

# Built-in fault codes (docs/isa.md) and the explicit FAULT codes used by the
# example firmware (firmware/*.source.json, docs/firmware.md).
FAULT_NAMES = {
    1: "invalid opcode, operand or pin ownership",
    2: "PC outside the committed image",
    3: "bounded-wait timeout (WAITPIN/WAITEVENT reached LIMIT)",
    4: "strict RX enqueue overflow (PUSH a=1 with RX full; word held in rx)",
    64: "uart-rx: framing error (low stop bit)",
    65: "i2c controller: address or data NACK",
    66: "i2c target: address or direction mismatch",
    67: "i2c target-read: controller ACKed the last byte",
}

ISA_VERSION = 2  # READ_SELECT 7 of the design of record

# The design of record (configs/instruction-sram-32.json "architecture").
DESIGN_ARCHITECTURE = {
    "schema_version": "protocol-emulator.architecture.v1",
    "engine_count": 4,
    "data_width": 32,
    "program_words": 64,
    "fifo_words": 8,
    "issue": "fused",
    "prefetch": False,
}

ARCHITECTURE_KEYS = ("schema_version", "engine_count", "data_width", "program_words",
                     "fifo_words", "issue", "prefetch")


def fault_name(code):
    """Human-readable name of an engine fault code (0 = no fault)."""
    if code == 0:
        return "no fault"
    name = FAULT_NAMES.get(code)
    if name is None:
        return "firmware FAULT %d" % code
    return name


def ui_byte(window, wvalid=False, rready=False, nibble=0):
    """The ui_in byte for one cycle."""
    return ((window & 3) << UI_WINDOW_SHIFT) | (UI_WVALID if wvalid else 0) \
        | (UI_RREADY if rready else 0) | (nibble & 15)


def command_word(op, payload=0):
    if not 0 <= op <= 255 or not 0 <= payload < (1 << 24):
        raise ValueError("command op or payload out of range")
    return (op << 24) | payload


def nibbles(word):
    """The eight little-endian nibbles of a 32-bit word (first sent first)."""
    return [(word >> (4 * i)) & 15 for i in range(8)]


def own_payload(pins, open_drain=0):
    if not 0 <= pins <= 255 or not 0 <= open_drain <= 255:
        raise ValueError("pin masks are 8 bits")
    return pins | (open_drain << 8)


def route_payload(source, destination, count, enable=True):
    if not 0 <= source <= 3 or not 0 <= destination <= 3:
        raise ValueError("route endpoints are engines 0..3")
    if not 0 <= count <= 0xFFFF:
        raise ValueError("route word count is 16 bits")
    return source | (destination << 2) | (16 if enable else 0) | (count << 5)


def trigger_payload(pin, mode, enable=True):
    if not 0 <= pin <= 7 or not 0 <= mode <= 3:
        raise ValueError("trigger pin 0..7, mode 0..3")
    return pin | (mode << 3) | (32 if enable else 0)


def split_levels(word):
    """READ_SELECT 2 word -> (tx_level, rx_level)."""
    return word & 0xFFFF, (word >> 16) & 0xFFFF


class Status:
    """Decoded READ_SELECT 0 word of one engine."""

    def __init__(self, word, engine=None):
        self.word = word
        self.engine = engine
        self.running = bool(word & 1)
        self.committed = bool(word & 2)
        self.stalled = bool(word & 4)
        self.faulted = bool(word & 8)
        self.fault = (word >> 8) & 0xFF if word & 8 else 0

    @property
    def fault_name(self):
        return fault_name(self.fault)

    def __eq__(self, other):
        return isinstance(other, Status) and other.word == self.word

    def __repr__(self):
        flags = []
        for name in ("running", "committed", "stalled"):
            if getattr(self, name):
                flags.append(name)
        if self.faulted:
            flags.append("fault %d (%s)" % (self.fault, self.fault_name))
        where = "" if self.engine is None else " engine %d" % self.engine
        return "<Status%s 0x%08x %s>" % (where, self.word, ", ".join(flags) or "halted")


def decode_uo(uo):
    """uo_out byte -> dict of the host-visible flags (for logging)."""
    return {"nibble": uo & 15, "write_ready": bool(uo & UO_WREADY),
            "read_valid": bool(uo & UO_RVALID), "irq": bool(uo & UO_IRQ),
            "fault": bool(uo & UO_FAULT)}
