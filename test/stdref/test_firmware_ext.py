# Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
"""The firmware/ext demos on the reference, checked against standard frames.

No RTL here: the demos run on ``program.ProgramRef`` and their predicted pads
are decoded with ``decode_pad_trace`` and compared with frames built by the
standard encoders.  test/test_line_stdref.py runs the same scenarios on the RTL.
"""

import random

import pytest

from _ref import ref
import demos
from program import ProgramRef


def pad_trace(prog, pin, length):
    return [(prog.output_at(n) >> pin) & 1 for n in range(length)]


def issue_of(outcome, pc, nth=0):
    cycles = [cycle for p, cycle in outcome.issue_cycles if p == pc]
    return cycles[nth]


@pytest.mark.parametrize("init", [0xFFFF, 0x0000])
def test_crc16_stream(init):
    img = demos.load_image("crc16-stream")
    rng = random.Random(init)
    words = [rng.randrange(1 << 32) for _ in range(3)]
    tx, expected = demos.crc16_stream_job(init, words)
    prog = ProgramRef(img.words, owned=img.owned, tx_words=tx)
    out = prog.run(0)
    assert out.fault == -2                              # waiting for the next job
    assert [w for _, w in out.pushes] == [expected]
    ltim_cycle = issue_of(out, 1)
    trace = pad_trace(prog, 0, out.end_cycle + 4)
    ticker = ref.Ticker(ltim_cycle, ref.Ltim.decode(1))
    for word, xfer in zip(words, out.xfers):
        k0 = ticker.first_after(xfer.cells[0].boundary - 1, 0)
        decoded = ref.decode_pad_trace(trace, ltim_cycle, 1, 0, k0, count=32, msb_first=True,
                                       check_until=xfer.complete + 2)
        assert decoded.violations == []
        assert decoded.data == ref.word_to_bits(word, 32, True)


def test_10base_t_udp_frame():
    img = demos.load_image("10base-t-udp")
    frame = demos.udp_frame()
    words = demos.frame_words_lsb_first(frame)
    assert len(words) == 17
    prog = ProgramRef(img.words, owned=img.owned, tx_words=words, stop_cycle=4000)
    out = prog.run(0)
    assert out.fault == -1
    ltim_cycle = issue_of(out, 2)
    first = out.xfers[0].cells[0].boundary
    ticker = ref.Ticker(ltim_cycle, ref.Ltim.decode(2))
    k0 = ticker.first_after(first - 1, 0)
    length = out.end_cycle
    tdp, tdm = pad_trace(prog, 0, length), pad_trace(prog, 1, length)
    last = out.xfers[-1].cells[-1].boundary
    decoded = ref.decode_pad_trace(tdp, ltim_cycle, 2, 130, k0, pair_trace=tdm, count=608,
                                   check_until=last + 1 + 4)
    assert decoded.violations == []
    assert decoded.data == ref.ethernet_line_bits(frame)
    assert ref.CRC32_ETHERNET.residue_of(decoded.data[64:]) == ref.CRC32_ETHERNET.residue
    # after the frame: TD+ high (start of TP_IDL), then both low
    after = tdp[last + 1 + 4:last + 1 + 40]
    assert after[0] in (0, 1) and 1 in after and after[-1] == 0


def test_10base_t_link_pulse_when_idle():
    img = demos.load_image("10base-t-udp")
    prog = ProgramRef(img.words, owned=img.owned, tx_words=[], stop_cycle=200)
    out = prog.run(0)
    tdp = pad_trace(prog, 0, 200)
    highs = [n for n, v in enumerate(tdp) if v]
    assert highs and highs[-1] - highs[0] + 1 == len(highs) == 4    # 100 ns at 40 MHz


def usb_run(token_start=300, data=bytes(range(0x10, 0x18)), length=12000):
    img = demos.load_image("usb-ls-in-responder")
    dplus, dminus = demos.usb_host_packet(token_start, demos.usb_in_token(0x70, 4), length)
    holder = {}

    def pad_in(n):
        prog = holder["prog"]
        oe = prog.oe_at(n)
        own = prog.output_at(n)
        value = 0
        for pin, host in ((0, dplus), (1, dminus)):
            level = (own >> pin) & 1 if (oe >> pin) & 1 else (host[n] if n < length else host[-1])
            value |= level << pin
        return value

    prog = ProgramRef(img.words, owned=img.owned, tx_words=demos.usb_data1_words(data),
                      pad_in=pad_in, stop_cycle=length - 100)
    holder["prog"] = prog
    return img, prog, prog.run(0), data


def test_usb_in_token_answered_with_data1():
    img, prog, out, data = usb_run()
    assert out.fault in (-1, -2)
    # the token was received: 7 SYNC bits, PID, 16 token bits, SE0
    token = out.xfers[:4]
    assert token[1].data_received == ref.usb_pid_bits(0x69)
    assert token[2].data_received == ref.usb_token_body(0x70, 4)
    assert token[3].se0 and token[3].bits_left == 8
    response = out.xfers[4:8]
    ltim_cycle = issue_of(out, 27)
    ticker = ref.Ticker(ltim_cycle, ref.Ltim.decode(6269712))
    k0 = ticker.first_after(response[0].cells[0].boundary - 1, 0)
    length = out.end_cycle
    dp, dm = pad_trace(prog, 0, length), pad_trace(prog, 1, length)
    last = response[-1].cells[-1].boundary
    expected = ref.usb_packet_bits("DATA1", ref.usb_data_body(data))
    decoded = ref.decode_pad_trace(dp, ltim_cycle, 6269712, 213, k0, pair_trace=dm,
                                   count=len(expected), level=0, check_until=last + 1 + 33)
    assert decoded.violations == []
    assert decoded.data == expected
    assert decoded.stuff_errors == []
    # EOP: SE0 then J, then the pins are released
    se0 = [n for n in range(last + 1, length) if dp[n] == 0 and dm[n] == 0]
    assert se0 and se0[-1] - se0[0] + 1 == len(se0)
    assert dp[se0[-1] + 1] == 0 and dm[se0[-1] + 1] == 1          # J


def test_usb_stuffed_response():
    """Data 0xFF bytes: the response carries stuff bits."""
    img, prog, out, data = usb_run(data=b"\xff" * 8)
    response = out.xfers[4:8]
    ltim_cycle = issue_of(out, 27)
    ticker = ref.Ticker(ltim_cycle, ref.Ltim.decode(6269712))
    k0 = ticker.first_after(response[0].cells[0].boundary - 1, 0)
    length = out.end_cycle
    dp, dm = pad_trace(prog, 0, length), pad_trace(prog, 1, length)
    expected = ref.usb_packet_bits("DATA1", ref.usb_data_body(data))
    decoded = ref.decode_pad_trace(dp, ltim_cycle, 6269712, 213, k0, pair_trace=dm,
                                   count=len(expected), level=0,
                                   check_until=response[-1].cells[-1].boundary + 1 + 33)
    assert decoded.data == expected and decoded.violations == []
    assert len(decoded.stuff_cells) >= 10


def can_run(nodes, tx_words, length):
    img = demos.load_image("can-node")
    holder = {}

    def device_level(n):
        prog = holder["prog"]
        if (prog.oe_at(n) >> 0) & 1:
            return (prog.output_at(n) >> 0) & 1
        return 1

    bus = demos.Bus(device_level, nodes)

    def pad_in(n):
        prog = holder["prog"]
        return ((prog.output_at(n) & 1) | (bus.level(n) << 1)) if n >= 0 else 2

    prog = ProgramRef(img.words, owned=img.owned, tx_words=tx_words, pad_in=pad_in,
                      stop_cycle=length)
    holder["prog"] = prog
    out = prog.run(0)
    bus.level(length)
    return prog, out, bus


def test_can_node_transmits_a_frame_that_a_receiver_acks():
    data = bytes([0xDE, 0xAD, 0xBE, 0xEF, 0x00, 0x00, 0x1F, 0x80])
    receiver = demos.CanReceiverNode(100)
    prog, out, bus = can_run([receiver], demos.can_tx_words(0x123, data), 16000)
    assert out.fault == -1                     # back in the idle loop, no fault 70/72
    frame = receiver.frames[0]
    assert frame["crc_ok"] and frame["ident"] == 0x123 and frame["data"] == data
    assert frame["fields"] == ref.can_frame(0x123, data)["fields"]


def test_can_node_receives_and_acks():
    data = bytes([1, 2, 3, 4, 0xF0, 0x0F, 0xFF, 0x00])
    sender = demos.CanTransmitterNode(0x2A5, data, 100, start=600)
    prog, out, bus = can_run([sender], [], 16000)
    assert out.fault == -1
    assert sender.ack_seen is True
    fields = ref.can_frame(0x2A5, data)["fields"]
    pushed = [w for _, w in out.pushes]
    assert len(pushed) == 3
    assert pushed[0] & ((1 << 19) - 1) == ref.bits_to_int(fields[:19], lsb_first=False)
    assert pushed[1] == int.from_bytes(data[:4], "big")
    assert pushed[2] == int.from_bytes(data[4:], "big")


def test_can_node_loses_arbitration_and_faults_72():
    data = bytes(8)
    rival = demos.CanTransmitterNode(0x122, bytes([0x55] * 8), 100, sync_on_sof=True)
    prog, out, bus = can_run([rival], demos.can_tx_words(0x123, data), 6000)
    assert out.fault == 72
    assert out.unit.flag_lost
