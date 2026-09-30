# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Host-port constants and word encoders for ISA v2 (docs/isa.md, "Host interface"),
the READ_SELECT 7 capability bits of the line-unit extension and the known devices.

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

# READ_SELECT 7 (docs/isa.md, "ISA version" and "Discovery"): bits 7..0 hold the
# ISA version, bits 23..8 constant capability bits (all 0 on a device without
# the line unit), bits 31..24 read 0. Compare only bits 7..0 with an ISA version.
VERSION_MASK = 0xFF
CAPABILITY_SHIFT = 8
CAPABILITY_MASK = 0xFFFF

# Capability bits, numbered within bits 23..8 (bit 0 here is READ_SELECT 7 bit 8).
CAP_LINE_UNIT = 0x0001      # [8]  line unit (docs/isa.md, "Line-unit extension")
CAP_FRACTION = 0x0002       # [9]  ticker fraction (LTIM Q)
CAP_STUFFING = 0x0004       # [10] bit stuffing (LCFG [2])
CAP_ARBITRATION = 0x0008    # [11] arbitration monitor (LCFG [8])
CAP_CRC16 = 0x0010          # [12] CRC-16 (CRC, XFER c bit 6)
CAP_CRC32 = 0x0020          # [13] CRC-32
CAP_CRC_PRESETS = 0x0040    # [14] CRC polynomial presets (CRC c = 3)
CAP_LINE_ENGINES = 0x0F00   # [19:16] one bit per engine that has the unit
CAP_LINE_ENGINE_SHIFT = 8
CAP_FEATURES = 0x007F       # the feature bits [14:8]
CAP_RESERVED = 0xF080       # [15] and [23:20]: no meaning assigned, read 0

CAPABILITY_NAMES = (("line_unit", CAP_LINE_UNIT), ("fraction", CAP_FRACTION),
                    ("stuffing", CAP_STUFFING), ("arbitration", CAP_ARBITRATION),
                    ("crc16", CAP_CRC16), ("crc32", CAP_CRC32), ("crc_presets", CAP_CRC_PRESETS))

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

# Known devices (ProtocolEmulator(device=...)): the READ_SELECT 7 word each one
# reads and whether READ_SELECT 5 counts completed instructions.
#   base         the design of record, configs/instruction-sram-32.json: ISA 2,
#                no capability bits
#   diet8_rec16  the line-unit extension variant, configs/variants/diet8_rec16.json
#                (docs/extension.md): ISA 3 (no debug counters), capability bits
#                0x0F5F (every feature but CRC-32, on engines 0-3)
DEVICES = {
    "base": {"version_word": 0x00000002, "debug_counters": True,
             "architecture": DESIGN_ARCHITECTURE},
    "diet8_rec16": {"version_word": 0x000F5F03, "debug_counters": False,
                    "architecture": DESIGN_ARCHITECTURE},
}


def split_version(word):
    """READ_SELECT 7 word -> (ISA version, capability bits)."""
    return word & VERSION_MASK, (word >> CAPABILITY_SHIFT) & CAPABILITY_MASK


def capability_mask(names):
    """Capability bits of a list of feature names (CAPABILITY_NAMES)."""
    mask = 0
    known = dict(CAPABILITY_NAMES)
    for name in names:
        if name not in known:
            raise ValueError("unknown capability %r" % (name,))
        mask |= known[name]
    return mask


def capability_names(mask):
    """Feature names of the feature bits in mask, in bit order."""
    return [name for name, bit in CAPABILITY_NAMES if mask & bit]


class Capabilities:
    """Decoded READ_SELECT 7 bits 23..8 (docs/isa.md, "Discovery")."""

    def __init__(self, bits):
        self.bits = bits & CAPABILITY_MASK

    @classmethod
    def from_word(cls, word):
        return cls(split_version(word)[1])

    @property
    def line_unit(self):
        return bool(self.bits & CAP_LINE_UNIT)

    @property
    def line_engines(self):
        """Mask of the engines that have the line unit."""
        return (self.bits & CAP_LINE_ENGINES) >> CAP_LINE_ENGINE_SHIFT

    @property
    def features(self):
        return self.bits & CAP_FEATURES

    def names(self):
        return capability_names(self.bits)

    def missing(self, required, engine=None):
        """What the device lacks for the feature bits ``required`` on ``engine``,
        as a list of descriptions (empty when nothing is missing)."""
        out = ["capability " + name for name in capability_names(required & ~self.bits)]
        if required & CAP_FEATURES and engine is not None and not (self.line_engines >> engine) & 1:
            out.append("the line unit on engine %d" % engine)
        return out

    def problems(self, engine_count=4):
        """Inconsistencies in the bits (empty for a well-formed capability field)."""
        out = []
        if self.bits & CAP_RESERVED:
            out.append("reserved capability bits 0x%04x set" % (self.bits & CAP_RESERVED))
        if self.features & ~CAP_LINE_UNIT and not self.line_unit:
            out.append("line-unit features %s without the line unit"
                       % ", ".join(capability_names(self.features & ~CAP_LINE_UNIT)))
        if self.line_unit != bool(self.line_engines):
            out.append("line-unit bit %d but engine mask 0x%x"
                       % (int(self.line_unit), self.line_engines))
        if self.line_engines >> engine_count:
            out.append("line-unit engine mask 0x%x names engines beyond %d"
                       % (self.line_engines, engine_count - 1))
        return out

    def __eq__(self, other):
        return isinstance(other, Capabilities) and other.bits == self.bits

    def __repr__(self):
        if not self.bits:
            return "<Capabilities none>"
        return "<Capabilities 0x%04x %s; line unit on engine mask 0x%x>" % (
            self.bits, ", ".join(self.names()) or "no features", self.line_engines)


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
