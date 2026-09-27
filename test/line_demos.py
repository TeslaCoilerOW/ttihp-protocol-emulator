# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The extension study's demo scenarios on the whole chip (docs/extension.md;
docs/extension-study.md sections 5.2 and 7), runnable on the DUT
(test_line_demos.py) or on the model alone.

Each scenario loads the firmware/ext images through the host port, sets
routes and prefills queues, STARTs the protocol engine and then performs no
host transaction while the chip runs (the free-running clock of the study's
risk R1). Only afterwards does the host read status and queues again. Pad
waveforms are checked against independent protocol references
(line_support.py): a Manchester/Ethernet decoder with CRC-32, a behavioural
CAN node on a wired-AND bus, and a USB low-speed host with NRZI, bit
stuffing and CRC16.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness import ROUTE, RS_STATUS, SELECT, START, Harness
from line_scenarios import Pads, engine_status, load_and_start, read_rx
from line_support import (PREAMBLE, PULL, PUSH_STRICT, RX, TX, X, Y, CanBus, CanOther, add, and_, assemble,
                          bits_lsb, can_line, can_loss_bit, can_rx_expect, can_rx_words, can_tx_words,
                          crc_bits_reflected, crc_catalogue, crc_get, crc_preset, dir_, fault, jmp, jz, lcfg,
                          limit, load, load_ext_image, lstat, ltim, mov, not_, pins, set_, shl, udp_frame,
                          usb_crc16, usb_token_line, waitpin, words_be, words_le, xline, xor)
from scenarios import fault_of

# ------------------------------------------------------------------ helpers


async def route(h: Harness, source: int, dest: int, count: int = 0xFFFF) -> None:
    await h.command(ROUTE, source | dest << 2 | 1 << 4 | count << 5)


async def relay_chain(h: Harness, relays: int) -> None:
    """Engines 1..relays run the relay image; ROUTE k -> k-1 (docs/extension-study.md 7.1)."""
    relay = load_ext_image("relay")["words"]
    for k in range(1, relays + 1):
        await h.load(k, relay)
        await route(h, k, k - 1)
    if relays:
        await h.command(START, ((1 << (relays + 1)) - 1) & 0xE)


async def stage(h: Harness, engine: int, words: list[int], max_wait: int = 400) -> int:
    """Write words into engine's TX queue until one is not accepted; the number staged."""
    await h.command(SELECT, engine)
    for n, word in enumerate(words):
        if not await h.try_write(2, word, max_wait=max_wait):
            return n
    return len(words)


def core_scope(h: Harness):  # noqa: ANN201 - a cocotb handle or None
    dut = getattr(h, "dut", None)
    if dut is None:
        return None
    scope = dut
    for name in ("user_project", "core"):
        scope = getattr(scope, name, None)
        if scope is None:
            return None
    return scope


# ------------------------------------------------------------------ 10BASE-T
@dataclass
class EthernetResult:
    staged: int
    words: int
    decoded: list[int]
    valid: bool
    start: int
    pads: Pads


async def ethernet(h: Harness, *, relays: int = 1, negative: bool = False, until_nlp: bool = True) -> None:
    """10base-t-udp on engine 0 with ``relays`` relay engines: a 68-byte UDP frame
    (17 queue words) behind the on-chip preamble/SFD, Manchester at 4 cycles
    per bit with no gap, decoded from TD+/TD- (pins 0/1): exact bytes, CRC-32
    residue 0xDEBB20E3, TP_IDL, and the first link pulse 640,000 cycles later.
    ``negative``: stage with one relay fewer than needed; the frame must break
    after the staged words (negative control of the no-gap check)."""
    await h.start()
    pads = Pads(lambda cycle, pads: 0xFF)
    h.pins = pads
    frame = udp_frame(b"TT protocol emulator")
    words = words_le(frame)
    assert len(words) == 17, "the image's COUNT 16 expects 17 frame words"
    depth = h.model.config.fifo_words
    await relay_chain(h, relays)
    image = load_ext_image("10base-t-udp")
    await h.load(0, image["words"], ownership=image["owned_pins"])
    staged = await stage(h, relays, words)
    capacity = depth + relays * (2 * depth + 1)
    assert staged == min(len(words), capacity), f"staged {staged}, capacity {capacity} (d + r(2d+1))"
    await h.idle(50)
    await h.command(START, 1)
    t_start = h.cycle
    # free run: no host transaction from here on
    span = 20_000 if negative else (660_000 if until_nlp else 4_000)
    await h.idle(span)
    end = h.cycle
    p = lambda t: pads.pad(t, 0, 0)  # noqa: E731
    m = lambda t: pads.pad(t, 1, 0)  # noqa: E731
    start = next((t for t in range(t_start, end) if p(t) != m(t)), end)
    all_bytes = PREAMBLE + frame
    nbits = 8 * len(all_bytes)
    valid = all(start + 4 * k + 3 < end and p(start + 4 * k) == 1 - p(start + 4 * k + 2)
                and p(start + 4 * k) == p(start + 4 * k + 1) and p(start + 4 * k + 2) == p(start + 4 * k + 3)
                and m(start + 4 * k) == 1 - p(start + 4 * k) for k in range(nbits))
    bits = [p(start + 4 * k + 2) if start + 4 * k + 2 < end else 0 for k in range(nbits)]
    decoded = [sum(bits[8 * k + j] << j for j in range(8)) for k in range(len(all_bytes))]
    if negative:
        good = 8 + 4 * staged
        assert staged < len(words), "negative control: the frame fits anyway"
        assert decoded[:good] == all_bytes[:good], "negative control: staged part of the frame is wrong"
        assert decoded != all_bytes or not valid, "negative control: the under-staged frame decoded intact"
        h.log("10BASE-T negative control: %d of %d words staged, frame breaks after byte %d", staged, len(words),
              good)
        return
    assert valid, "Manchester cells invalid (halves, pair or gap)"
    assert decoded == all_bytes, "decoded frame differs"
    residue = crc_bits_reflected([b for byte in decoded[8:] for b in bits_lsb(byte, 8)], poly=0xEDB88320,
                                 init=0xFFFFFFFF)
    assert residue == 0xDEBB20E3, f"CRC-32 residue {residue:#x}"
    finish = start + 4 * nbits
    high_end = finish
    while high_end < end and p(high_end) == 1 and m(high_end) == 0:
        high_end += 1
    assert 10 <= high_end - finish <= 14 and p(high_end) == 0 and m(high_end) == 0, \
        f"TP_IDL {high_end - finish} cycles"
    h.log("10BASE-T: %d words staged (%d relay(s)), %d-byte frame decoded, residue %#x, TP_IDL %d cycles",
          staged, relays, len(all_bytes), residue, high_end - finish)
    if until_nlp:
        pulse = next((t for t in range(high_end + 1, end) if p(t) == 1 and p(t - 1) == 0), -1)
        assert pulse > 0, "no link pulse"
        width = next(t for t in range(pulse, end) if p(t) == 0) - pulse
        assert abs(pulse - high_end - 640_000) < 40 and width == 4, \
            f"NLP at +{pulse - high_end} cycles, {width} cycles wide"
        h.log("10BASE-T: first NLP %d cycles after TP_IDL, %d cycles wide", pulse - high_end, width)
    status = await engine_status(h, 0)
    assert status & 1 and not status & 8, f"engine 0 status {status:#x}"


# ------------------------------------------------------------------ CAN
CAN_A = (0x2A5, [0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x23, 0x45, 0x67])
CAN_B = (0x155, [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])   # beats 0x3A5
CAN_C = (0x7A5, [0x0F, 0x1E, 0x2D, 0x3C, 0x4B, 0x5A, 0x69, 0x78])   # loses to 0x120
B8 = (0x3A5, [0x12, 0x9A, 0xF0, 0x0F, 0x55, 0xAA, 0x00, 0xFF])
C5 = (0x0F0, [0x01, 0x02, 0x03, 0x04, 0x05])
D8 = (0x111, [0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80])
Z0 = (0x3F0, [])
F4 = (0x001, [0xA5, 0x5A, 0xC3, 0x3C])
O1 = (0x555, [0x00])
Y1 = (0x120, [0x5A])


def can_node_words(rev: int = 3) -> list[int]:
    """The can-node listing (docs/extension-study.md 7.2), encoded here
    independently of the assembler. rev=3 must equal firmware/ext/can-node;
    rev=2 is the earlier listing, a negative control that misses an SOF sent
    at the minimum spacing after its own frame."""
    r2 = rev == 2
    dsm = dict(msb=True, drive=True, sample=True)
    items = [
        pins(2, 0, 1), set_(1), dir_(1), limit(0xFFFFFF), crc_preset(2), not_(Y),
        "idle", lcfg(stuff=5, either=True, arb=True, init=1), lstat(X), load(TX, 8), and_(X, TX),
        lambda l: jz(X, l("rx")),
        ltim(50) if r2 else ltim(50, delay=150), PULL, xline(19, crc=True, **dsm),
        lstat(X), load(TX, 2), and_(X, TX), lambda l: jz(X, l("tx_ok")), fault(72),
        "tx_ok", PULL, xline(32, crc=True, **dsm), PULL, xline(32, crc=True, **dsm),
        crc_get(TX), shl(TX, 16), xline(15, crc=True, **dsm),
        lcfg(init=1), load(TX, 0xFFFF), shl(TX, 16), xline(14 if r2 else 12, **dsm),
        mov(X, RX), load(TX, 0x1000 if r2 else 0x400), and_(X, TX), lambda l: jz(X, l("idle")), fault(70),
        "rx", waitpin(1, 0), ltim(50, delay=1), xline(19, msb=True, sample=True, crc=True),
        PUSH_STRICT, mov(X, RX), load(TX, 15), and_(X, TX),
        "byte", lambda l: jz(X, l("crc")), xline(8, msb=True, sample=True, crc=True), add(X, Y),
        load(TX, 4), xor(TX, X), lambda l: jz(TX, l("flush")), lambda l: jz(X, l("flush")),
        lambda l: jmp(l("byte")),
        "flush", PUSH_STRICT, lambda l: jmp(l("byte")),
        "crc", xline(15, msb=True, sample=True, crc=True), lcfg(init=1), xline(1, msb=True, sample=True),
        crc_get(X), lambda l: jz(X, l("ack")), fault(71),
        "ack", load(TX, 0x7FFF), shl(TX, 16),
        xline(13, msb=True, drive=True) if r2 else xline(11, **dsm),
        lambda l: jmp(l("idle")),
    ]
    return assemble(items)


@dataclass
class CanSession:
    staged: int
    code: int
    got: list[int]
    bus: CanBus
    pads: Pads
    t_start: int
    drain: bool

    def txd(self, t: int) -> int:
        return self.pads.pad(t, 0, 1)

    def acks(self, frames: list) -> list[str]:
        """Problems with our ACK of the other node's received frames (empty: none).

        For each frame the bus saw (start cycle s, stuffed length n), in bus
        time (our TXD reaches the bus 4 cycles after the pad): our TXD is
        dominant at the middle of the ACK slot, recessive at the middles of the
        CRC delimiter and the ACK delimiter, and dominant only within the ACK
        slot (95..105 dominant cycles, all inside it) from the frame's SOF to
        the end of its intermission."""
        problems = []
        if len(self.bus.sent) != len(frames):
            return [f"{len(self.bus.sent)} frames sent, {len(frames)} expected"]
        for (ident, start), frame in zip(self.bus.sent, frames):
            n = len(can_line(frame))
            ours = lambda t: self.txd(t - 4)  # noqa: E731 (bus time)
            slot = start + 100 * (n + 1)
            crc_delimiter, ack, ack_delimiter = ours(slot - 50), ours(slot + 50), ours(slot + 150)
            dominant = [t for t in range(start, start + 100 * (n + 12)) if ours(t) == 0]
            inside = all(slot - 5 <= t < slot + 110 for t in dominant)
            if not (ack == 0 and crc_delimiter == 1 and ack_delimiter == 1 and inside
                    and 95 <= len(dominant) <= 105):
                problems.append(f"frame {ident:#x}: CRC delimiter {crc_delimiter}, ACK {ack}, ACK delimiter "
                                f"{ack_delimiter}, {len(dominant)} dominant cycles, all in the ACK slot: {inside}")
        return problems


async def can_session(h: Harness, *, ours: list, theirs: list[CanOther], rev: int = 3,
                      rx_drain: bool | None = None, until: int | None = None) -> CanSession:
    """can-node on engine 0 (TXD pin 0, RXD pin 1) against CanBus; TX words
    prefilled (through a relay on engine 2 when they exceed the queue), RX
    through a drain relay (engine 0 -> 1) when the expected words exceed it."""
    await h.start()
    bus = CanBus([tuple(f) for f in ours], theirs)
    pads: Pads

    def env(cycle: int, pads: Pads) -> int:
        # our TXD reaches the bus through a 5-cycle transceiver loop (pull-up when released)
        return 0xFD | bus.step(cycle, pads.pad(cycle - 4, 0, 1)) << 1

    pads = Pads(env)
    h.pins = pads
    depth = h.model.config.fifo_words
    words = [w for f in ours for w in can_tx_words(*f)]
    rx_words = sum(can_rx_words((o.ident, o.data)) for o in theirs)
    feed = 0 if depth >= len(words) else 2
    drain = rx_drain if rx_drain is not None else rx_words > depth
    relay = load_ext_image("relay")["words"]
    if feed:
        await h.load(2, relay)
        await route(h, 2, 0)
    if drain:
        await h.load(1, relay)
        await route(h, 0, 1)
    if feed or drain:
        await h.command(START, (4 if feed else 0) | (2 if drain else 0))
    program = load_ext_image("can-node")["words"] if rev == 3 else can_node_words(rev)
    await h.load(0, program, ownership=0x01)
    staged = await stage(h, feed, words)
    await h.idle(50)
    await h.command(START, 1)
    t_start = h.cycle
    bits = sum(len(can_line(f)) + 13 for f in ours) + 2 * sum(len(can_line((o.ident, o.data))) + 13 for o in theirs)
    await h.idle(until if until is not None else 100 * bits + 3000)
    if until is not None:
        return CanSession(staged, 0, [], bus, pads, t_start, drain)
    code = fault_of(await engine_status(h, 0))
    got = []
    source = 1 if drain else 0
    await h.command(SELECT, source)
    while True:
        word = await h.try_read(3, max_wait=60)
        if word is None:
            break
        got.append(word)
    return CanSession(staged, code, got, bus, pads, t_start, drain)


def our_frames(session: CanSession, ours: list) -> list[tuple[int, bool]]:
    """(TXD start cycle, exact) per frame of ours, sampled mid-bit from each SOF the bus saw."""
    out = []
    for k, frame in enumerate(ours):
        if k >= len(session.bus.our_sofs):
            out.append((-1, False))
            continue
        start = session.bus.our_sofs[k] - 4
        line = can_line(frame)
        out.append((start, all(session.txd(start + 100 * j + 50) == b for j, b in enumerate(line))))
    return out


def rx_ok(session: CanSession, frames: list) -> bool:
    expect = [pair for f in frames for pair in can_rx_expect(*f)]
    return len(session.got) == len(expect) and all(g & mask == value for g, (value, mask) in zip(session.got, expect))


async def can_tx_rx(h: Harness, frames: list, spacing: list[int], *, rev: int = 3) -> CanSession:
    """TX of CAN_A (8 bytes, ACKed), then RX of ``frames``, which the other node
    starts ``spacing`` recessive bits after the last dominant bit (11 = end of
    intermission, 10 = the 3rd intermission bit, the minimum)."""
    session = await can_session(h, ours=[CAN_A], theirs=[CanOther(i, d, r) for (i, d), r in zip(frames, spacing)],
                                rev=rev)
    return session


async def can_rx_min_spacing(h: Harness) -> None:
    for frames, spacing in (([B8, C5], [11, 10]), ([Z0, F4, O1], [10, 11, 10])):
        s = await can_tx_rx(h, frames, spacing)
        tx = all(ok for _, ok in our_frames(s, [CAN_A]))
        assert s.staged == 3 and tx, "our frame on TXD differs from the stuffed CRC-15 reference"
        assert s.code == 0, f"fault {s.code}"
        assert rx_ok(s, frames), f"RX words {[hex(w) for w in s.got]}"
        assert not s.bus.errors and not s.bus.lost and len(s.bus.sent) == len(frames), \
            f"bus errors {s.bus.errors} lost {s.bus.lost} sent {s.bus.sent}"
        assert not s.acks(frames), f"ACK of received frames: {s.acks(frames)}"
        h.log("CAN: TX + ACK, then RX of DLC %s at spacing %s -> %d packed words, each ACKed in its ACK slot",
              [len(d) for _, d in frames], spacing, len(s.got))


async def can_back_to_back(h: Harness) -> None:
    """Two queued frames back to back; the other node's simultaneous SOF (0x3A5)
    loses at the stuffed bit where 0x155 differs, retransmits at the end of
    intermission and is received. The 2nd SOF of ours is 0..20 cycles after the
    end of intermission."""
    s = await can_session(h, ours=[CAN_A, CAN_B], theirs=[CanOther(*B8, 11)])
    frames = our_frames(s, [CAN_A, CAN_B])
    assert all(ok for _, ok in frames), "our frames on TXD differ from the reference"
    offset = frames[1][0] - frames[0][0] - 100 * (len(can_line(CAN_A)) + 13)
    loss = can_loss_bit(B8, CAN_B)
    assert s.staged == 6 and 0 <= offset <= 20 and s.code == 0, f"staged {s.staged} offset {offset} fault {s.code}"
    assert [(i, k) for i, _, k in s.bus.lost] == [(0x3A5, loss)], f"lost {s.bus.lost}"
    assert [i for i, _ in s.bus.sent] == [0x3A5] and not s.bus.errors, f"sent {s.bus.sent} errors {s.bus.errors}"
    assert rx_ok(s, [B8]), f"RX words {[hex(w) for w in s.got]}"
    assert not s.acks([B8]), f"ACK of the received frame: {s.acks([B8])}"
    h.log("CAN back to back: 2nd SOF %d cycles after the end of intermission; 0x3A5 lost at bit %d", offset, loss)


async def can_arbitration_lost(h: Harness) -> None:
    """Our 2nd frame (0x7A5) loses to a simultaneous SOF (0x120): fault 72,
    TXD recessive from the lost bit on, the winner's frame completes."""
    s = await can_session(h, ours=[CAN_A, CAN_C], theirs=[CanOther(*Y1, 11)])
    frames = our_frames(s, [CAN_A, CAN_C])
    loss = can_loss_bit(CAN_C, Y1)
    start = frames[1][0]
    assert frames[0][1] and start >= 0, "first frame differs or second not started"
    line = can_line(CAN_C)
    prefix = all(j >= loss or s.txd(start + 100 * j + 50) == b for j, b in enumerate(line))
    end = h.cycle
    recessive = all(s.txd(t) == 1 for t in range(start + 100 * loss + 60, end - 100))
    assert prefix and recessive, f"prefix {prefix} recessive after loss {recessive}"
    assert s.code == 72, f"fault {s.code}"
    assert [i for i, _ in s.bus.sent] == [0x120] and not s.bus.lost and not s.bus.errors
    h.log("CAN arbitration: 0x7A5 lost to 0x120 at stuffed bit %d, fault 72", loss)


async def can_negative_rev2(h: Harness) -> None:
    """Negative control: the revision-2 listing misses an SOF sent at the minimum
    spacing after its own frame (fault 71 instead of receiving)."""
    s = await can_tx_rx(h, [B8, C5], [11, 10], rev=2)
    ok = s.code == 0 and rx_ok(s, [B8, C5]) and not s.bus.errors
    assert not ok, "the revision-2 listing passed the minimum-spacing case"
    h.log("CAN negative control (revision-2 listing): fault %d, RX %s", s.code, [hex(w) for w in s.got])


async def can_negative_overflow(h: Harness) -> None:
    """Negative control: RX at the minimum spacing without drain relay or host
    service overflows the RX queue: strict PUSH faults with code 4."""
    depth = h.model.config.fifo_words
    frames = [B8, D8, B8, D8, B8][: depth // 3 + 1]
    s = await can_session(h, ours=[CAN_A], theirs=[CanOther(i, d, 11) for i, d in frames], rx_drain=False)
    assert all(ok for _, ok in our_frames(s, [CAN_A])) and s.code == 4, f"fault {s.code}"
    h.log("CAN negative control: %d frames (%d pushes) into a %d-word RX queue: fault 4", len(frames),
          3 * len(frames), depth)


async def can_reset_mid_frame(h: Harness) -> None:
    """Reset for 2 cycles in the middle of a CAN frame: TXD released, engine 0
    idle with no fault; in the RTL every line-unit register reads 0."""
    s = await can_session(h, ours=[CAN_A], theirs=[], until=145 + 100 * 60)
    assert s.bus.our_sofs, "frame not started"
    scope = core_scope(h)
    from model.line_unit import LineState  # noqa: F401  (the model resets too)
    names = ["line_run", "line_phase", "line_frac", "line_acc", "line_boundary_seen", "line_cfg", "line_level",
             "line_rx_prev", "line_cell_bit", "line_man_pending", "line_se0", "line_remaining",
             "line_trailing_stuff", "stuff_run", "stuff_last", "stuff_error", "arbitration_lost", "crc_state",
             "crc_preset", "transfer_mode"]

    def registers() -> dict[str, int]:
        found = {}
        if scope is None:
            return found
        for base in names:
            for name in (base, *(f"{base}_{k}" for k in range(4))):
                handle = getattr(scope, name, None)
                if handle is not None:
                    value = handle.value  # LogicArray, or Logic for 1-bit registers
                    found[name] = value.to_unsigned() if hasattr(value, "to_unsigned") else int(value)
        return found

    before = registers()
    await h.reset(2)
    await h.idle(8)
    after = registers()
    assert h.last_pre.uio_oe == 0, "TXD not released"
    status = await engine_status(h, 0)
    assert not status & 1 and not status & 8, f"status {status:#x}"
    if scope is not None:
        assert len(after) == 4 * len(names), f"found {len(after)} registers"
        nonzero_before = [n for n, v in before.items() if v]
        assert len(nonzero_before) >= 5, f"only {nonzero_before} nonzero before the reset"
        assert not any(after.values()), f"nonzero after reset: {[n for n, v in after.items() if v]}"
        h.log("reset mid-frame: %d line-unit registers, %d nonzero before, all zero after", len(after),
              len(nonzero_before))


# ------------------------------------------------------------------ USB low speed
async def usb_in_responder(h: Harness) -> None:
    """usb-ls-in-responder: the host sends an IN token (NRZI, stuffed, CRC5) on
    D+/D- (pins 0/1); the engine answers with the queued DATA1 packet. Decoded
    from the pads: SYNC, PID 0x4B, 8 bytes and the CRC16 of the independent
    reference; turnaround between 2 and 6.5 bit times after the EOP."""
    await h.start()
    bit = 50_000_000 / 1_500_000
    line = usb_token_line(0x9, 0x3A, 1)
    token: dict[str, int | None] = {"at": None}

    def host(cycle: int) -> int:
        if token["at"] is None or cycle < token["at"]:
            return 2  # J: D- high
        k = int((cycle - token["at"]) / bit)
        if k < len(line):
            return 1 if line[k] else 2
        if k < len(line) + 2:
            return 0  # SE0
        return 2

    def env(cycle: int, pads: Pads) -> int:
        value, enable = pads.driven(cycle)
        pads_level = value & 3 if enable & 3 == 3 else host(cycle)
        return 0xFC | pads_level

    pads = Pads(env)
    h.pins = pads
    image = load_ext_image("usb-ls-in-responder")
    await h.load(0, image["words"], ownership=image["owned_pins"])
    data = [0x01, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00]
    staged = await stage(h, 0, [0x4B80, *words_le(data)])
    await h.idle(50)
    await h.command(START, 1)
    token["at"] = h.cycle + 200
    await h.idle(12_000)
    end = h.cycle

    def state(t: int) -> str:
        value, enable = pads.driven(t)
        if enable & 3 != 3:
            return "Z"
        return {1: "K", 2: "J", 0: "0", 3: "1"}[value & 3]

    first_k = next((t for t in range(token["at"], end) if state(t) == "K"), end)
    eop_j = token["at"] + (len(line) + 2) * bit
    gap = (first_k - eop_j) / bit
    raw, previous, k = [], "J", 0
    while True:
        t = first_k + int((k + 0.5) * bit)
        if t >= end or state(t) in "0Z":
            break
        raw.append(1 if state(t) == previous else 0)
        previous = state(t)
        k += 1
    bits, run, skip = [], 0, False
    for b in raw:
        if skip:
            skip, run = False, 0
            continue
        bits.append(b)
        run = run + 1 if b else 0
        if run == 6:
            skip = True
    got = [sum(bits[8 * k + j] << j for j in range(8)) for k in range(len(bits) // 8)]
    crc = usb_crc16(data)
    want = [0x80, 0x4B] + data + [crc & 255, crc >> 8]
    assert staged == 3 and got == want, f"packet {[hex(b) for b in got]} != {[hex(b) for b in want]}"
    assert 2 <= gap <= 6.5, f"turnaround {gap:.2f} bit times"
    assert fault_of(await engine_status(h, 0)) == 0
    h.log("USB IN responder: DATA1 + 8 bytes + CRC16 %#06x, turnaround %.2f bit times", crc, gap)


# ------------------------------------------------------------------ CRC stream
async def crc_stream(h: Harness) -> None:
    """crc16-stream: two jobs (init 0xFFFF: CRC-16/IBM-3740; init 0: XMODEM) over
    queued words; results equal the catalogue model over the same bytes."""
    await h.start()
    image = load_ext_image("crc16-stream")
    await h.load(0, image["words"], ownership=image["owned_pins"])
    first = list(b"The line unit computes CRC-16..!")  # 32 bytes
    second = list(b"12345678")
    jobs = [(0xFFFF, first), (0x0000, second)]
    await h.command(SELECT, 0)
    await h.command(START, 1)
    for init, data in jobs:  # the engine drains the queue while the host writes
        for word in [init, len(data) // 4, *words_be(data)]:
            await h.write(2, word)
    await h.idle(2 * 32 * 4 + 200)
    results = await read_rx(h, 0, 2)
    for (init, data), got in zip(jobs, results):
        want = crc_catalogue(bytes(data), width=16, poly=0x1021, init=init, refin=False, refout=False, xorout=0)
        assert got == want, f"CRC stream init {init:#x}: {got:#x} != {want:#x}"
    assert crc_catalogue(b"123456789", width=16, poly=0x1021, init=0xFFFF, refin=False, refout=False,
                         xorout=0) == 0x29B1
    h.log("crc16-stream: %s", [hex(r) for r in results])


# ------------------------------------------------------------------ USB: rejection paths
USB_BIT = 50_000_000 / 1_500_000


def usb_crc5_spec(field_bits: list[int]) -> list[int]:
    """USB 2.0 section 8.3.5: an MSB-first register seeded with ones, generator
    00101, the remainder inverted and sent MSB first (a formulation independent
    of line_support.usb_token_line's reflected one)."""
    reg = 0x1F
    for b in field_bits:
        feedback = (reg >> 4 & 1) ^ b
        reg = (reg << 1) & 0x1F
        if feedback:
            reg ^= 0x05
    reg ^= 0x1F
    return [reg >> (4 - i) & 1 for i in range(5)]


def usb_token_levels(pid: int, address: int, endpoint: int, corrupt_crc: bool = False) -> list[int]:
    """Line levels (1 = K) of SYNC, PID, ADDR, ENDP and CRC5, stuffed and NRZI coded from J."""
    fields = bits_lsb(address, 7) + bits_lsb(endpoint, 4)
    crc = usb_crc5_spec(fields)
    if corrupt_crc:
        crc[2] ^= 1
    bits = [0, 0, 0, 0, 0, 0, 0, 1] + bits_lsb(pid | (~pid & 15) << 4, 8) + fields + crc
    stuffed, run = [], 0
    for b in bits:
        stuffed.append(b)
        run = run + 1 if b else 0
        if run == 6:
            stuffed.append(0)
            run = 0
    level, out = 0, []  # J = 0
    for b in stuffed:
        if b == 0:
            level ^= 1
        out.append(level)
    return out


async def usb_session(h: Harness, tokens: list[tuple[int, list[int]]]) -> tuple[Pads, list[int], int]:
    """usb-ls-in-responder with one DATA1 packet queued; the host sends each
    token (cycle offset, line levels) followed by a 2-bit SE0 and J. Returns the
    pad recorder, the token start cycles and the end cycle."""
    await h.start()
    schedule: list[tuple[int, list[int]]] = []

    def host(cycle: int) -> int:
        for at, line in schedule:
            if cycle < at:
                continue
            k = int((cycle - at) / USB_BIT)
            if k < len(line):
                return 1 if line[k] else 2
            if k < len(line) + 2:
                return 0
        return 2

    def env(cycle: int, pads: Pads) -> int:
        value, enable = pads.driven(cycle)
        return 0xFC | (value & 3 if enable & 3 == 3 else host(cycle))

    pads = Pads(env)
    h.pins = pads
    image = load_ext_image("usb-ls-in-responder")
    await h.load(0, image["words"], ownership=image["owned_pins"])
    data = [0x01, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00]
    assert await stage(h, 0, [0x4B80, *words_le(data)]) == 3
    await h.idle(50)
    await h.command(START, 1)
    base = h.cycle + 200
    schedule.extend((base + offset, line) for offset, line in tokens)
    await h.idle(max(at for at, _ in schedule) - h.cycle + 12_000)
    return pads, [at for at, _ in schedule], h.cycle


def usb_state(pads: Pads, t: int) -> str:
    value, enable = pads.driven(t)
    if enable & 3 != 3:
        return "Z"
    return {1: "K", 2: "J", 0: "0", 3: "1"}[value & 3]


async def usb_rejects_then_answers(h: Harness) -> None:
    """An IN token with a wrong CRC5, an OUT token and a SETUP token get no
    answer (the bus stays undriven); a later valid IN token is answered, and
    the device's EOP is SE0 for 62..75 cycles (1.25-1.50 us), then J for 25..40
    cycles, then the pins are released."""
    gap = 8_000
    tokens = [(0, usb_token_levels(0x9, 0x3A, 1, corrupt_crc=True)), (gap, usb_token_levels(0x1, 0x3A, 1)),
              (2 * gap, usb_token_levels(0xD, 0x3A, 1)), (3 * gap, usb_token_levels(0x9, 0x3A, 1))]
    pads, starts, end = await usb_session(h, tokens)
    driven_early = [t for t in range(starts[0], starts[3]) if usb_state(pads, t) != "Z"]
    assert not driven_early, f"the device drove the bus at {driven_early[:3]} after a rejected token"
    first_k = next((t for t in range(starts[3], end) if usb_state(pads, t) == "K"), None)
    assert first_k is not None, "no answer to the valid IN token after the rejected ones"
    se0 = next(t for t in range(first_k, end) if usb_state(pads, t) == "0")
    se0_end = next(t for t in range(se0, end) if usb_state(pads, t) != "0")
    j_end = next(t for t in range(se0_end, end) if usb_state(pads, t) != "J")
    assert 62 <= se0_end - se0 <= 75, f"EOP SE0 {se0_end - se0} cycles"
    assert usb_state(pads, se0_end) == "J" and 25 <= j_end - se0_end <= 40 and usb_state(pads, j_end) == "Z", \
        f"after SE0: {usb_state(pads, se0_end)} for {j_end - se0_end} cycles, then {usb_state(pads, j_end)}"
    assert fault_of(await engine_status(h, 0)) == 0
    h.log("USB: bad CRC5, OUT and SETUP ignored; IN answered (first K %d cycles after the token), EOP SE0 %d "
          "cycles, J %d cycles, then released", first_k - starts[3], se0_end - se0, j_end - se0_end)


async def usb_any_address(h: Harness) -> None:
    """Records a limit of the firmware: it has no address or endpoint filter, so
    an IN token to address 0x05, endpoint 3 is answered as well."""
    pads, starts, end = await usb_session(h, [(0, usb_token_levels(0x9, 0x05, 3))])
    answered = any(usb_state(pads, t) == "K" for t in range(starts[0], end))
    assert answered, "an IN token to address 0x05 endpoint 3 was not answered"
    h.log("USB: IN to address 0x05, endpoint 3 answered (no address or endpoint filter)")
