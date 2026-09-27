# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Constrained-random programs for the line unit (docs/extension.md).

Each case gives up to four engines a random program built from line-unit
blocks (LTIM, LCFG, CRC, LSTAT, line and classic XFERs with the CRC bit)
mixed with base instructions (SET, DIR, OUT, WAIT, PULL/PUSH, ALU, TIME,
JZ loops), with a few deliberately invalid encodings. Engine k owns pins
2k and 2k+1. The environment drives undriven pads with random levels,
loopbacks from other pins and short SE0 episodes; the host feeds TX queues
and drains RX queues at random while the engines run.

The harness compares every cycle with the reference model, so the check is
the lockstep itself; ``LineReference.events`` counts what was exercised.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from harness import CLEAR, SELECT, START, STOP, Harness, pad_value
from line_support import (HALT, PULL, PUSH, RX, TX, X, Y, add, and_, crc_get, crc_preset, crc_set, dir_, ins, imm,
                          jmp, jz, lcfg, load, lstat, ltim, mov, not_, or_, pins, set_, shl, shr, wait, waitpin,
                          xfer, xline, xor)
from model.reference import Outputs

REGS = (TX, RX, X, Y)


@dataclass
class EngineProgram:
    engine: int
    words: list[int]
    ownership: int


@dataclass
class Case:
    seed: int
    programs: list[EngineProgram]
    tx_words: list[list[int]]
    cycles: int
    env_seed: int
    notes: list[str] = field(default_factory=list)


def _lcfg_random(rng: random.Random) -> tuple[int, dict]:
    code = rng.choice((0, 0, 1, 1, 2))
    stuff = rng.choice((0, 0, 1, 2, 3, 5, 6, 8))
    either = stuff >= 2 and rng.random() < 0.5
    cfg = dict(code=code, stuff=stuff, either=either, pair=rng.random() < 0.4, arb=rng.random() < 0.3,
               se0=rng.random() < 0.3, init=rng.randrange(2))
    return lcfg(**cfg), cfg


def _program(rng: random.Random, engine: int, fifo: int) -> EngineProgram:
    own = 3 << (2 * engine)
    base_pin = 2 * engine
    words: list[int] = []
    ck = base_pin + 1 if rng.random() < 0.7 else rng.randrange(8)
    out = base_pin
    inp = rng.randrange(8)
    words.append(pins(ck, out, inp))
    words.append(set_(rng.randrange(4) << base_pin))
    words.append(dir_(rng.choice((1, 3, 0)) << base_pin))
    word, cfg = _lcfg_random(rng)
    words.append(word)
    period = rng.choice((1, 2, 2, 3, 4, 5, 7))
    words.append(ltim(period, rng.choice((0, 0, 85, 128, 171, 255)), rng.choice((0, 1, 2, 3, 9))))
    words.append(crc_preset(rng.randrange(4)))
    words.append(load(X, rng.randrange(1 << 16)))
    words.append(crc_set(X))
    loop_start = len(words)
    body_len = rng.randrange(8, 64 - len(words) - 3)
    for _ in range(body_len):
        r = rng.random()
        if r < 0.28:  # line XFER, often with fresh TX data
            if rng.random() < 0.5:
                words.append(rng.choice((not_(TX), load(TX, rng.randrange(1 << 16)), PULL)))
            drive = rng.random() < 0.6
            sample = not drive or (rng.random() < 0.4 and cfg["code"] != 2)
            if cfg["code"] == 2:
                sample = False
                drive = True
            words.append(xline(rng.choice((1, 2, 3, 5, 8, 13, 16, 32)), msb=rng.random() < 0.5, drive=drive,
                               sample=sample, crc=rng.random() < 0.5))
        elif r < 0.33:  # classic XFER, sometimes with the CRC bit (stops the ticker)
            c = rng.choice((0x08, 0x10, 0x18, 0x0C, 0x4C, 0x50, 0x58, 0x49, 0x53))
            words.append(xfer(rng.choice((1, 3, 8)), rng.choice((1, 2)), c))
            words.append(ltim(rng.choice((1, 2, 3)), rng.randrange(256), rng.randrange(4)))  # restart it
        elif r < 0.38:
            words.append(lstat(rng.choice(REGS)))
        elif r < 0.43:
            words.append(crc_get(rng.choice(REGS)))
        elif r < 0.46:
            words.append(crc_set(rng.choice(REGS)))
        elif r < 0.48:
            words.append(crc_preset(rng.randrange(4)))
        elif r < 0.53:
            word, new_cfg = _lcfg_random(rng)
            words.append(word)
            cfg = new_cfg
        elif r < 0.56:
            words.append(ltim(rng.choice((0, 1, 2, 3, 6, 255)), rng.randrange(256) if rng.random() < 0.7 else 0,
                              rng.randrange(12)))
            if words[-1] & 0xFF == 255 and words[-1] >> 8 & 0xFF:
                words[-1] = ltim(254, rng.randrange(256))
            if words[-1] & 0xFF == 0:  # keep the ticker running mostly
                words.append(ltim(rng.choice((1, 2, 3))))
        elif r < 0.60:
            words.append(PULL)
        elif r < 0.64:
            words.append(PUSH)
        elif r < 0.67:
            words.append(set_(rng.randrange(4) << base_pin))
        elif r < 0.69:
            words.append(ins(8, rng.choice((base_pin, base_pin + 1)), 0, rng.randrange(2)))  # OUT
        elif r < 0.72:
            words.append(wait(rng.randrange(1, 12)))
        elif r < 0.80:
            op = rng.choice((18, 20, 21, 22, 23, 27))
            words.append(ins(op, rng.choice(REGS), rng.choice(REGS) if op != 27 else 0))
        elif r < 0.84:
            words.append(rng.choice((shl, shr))(rng.choice(REGS), rng.choice((0, 8, 16, 24))))
        elif r < 0.87:
            words.append(load(rng.choice(REGS), rng.randrange(1 << 16)))
        elif r < 0.89:
            words.append(ins(28, rng.choice(REGS)))  # TIME
        elif r < 0.91:
            words.append(dir_(rng.choice((1, 2, 3)) << base_pin))
        elif r < 0.93:
            words.append(pins(rng.choice((base_pin + 1, base_pin, rng.randrange(8))), base_pin, rng.randrange(8)))
        elif r < 0.95:
            words.append(waitpin(rng.randrange(8), rng.randrange(2)))
        else:  # a deliberately invalid or unusual encoding
            words.append(rng.choice((
                ltim(255, 1 + rng.randrange(255)), imm(31, 3), imm(31, 1 << 11), lcfg(stuff=1, either=True),
                ins(32, 0, 0, 0), ins(32, 1, 0, 1), ins(33, 4), xfer(8, 1, 0x30), xfer(8, 0, 0x31),
                xfer(8, 0, 0xB0), xfer(0, 0, 0x28), imm(34), xline(8, drive=True, sample=True))))
    if rng.random() < 0.8:
        words.append(jmp(loop_start))
    else:
        words.append(HALT)
    return EngineProgram(engine, words[:64], own)


def make_case(seed: int, *, fifo: int = 8, cycles: int = 6000) -> Case:
    rng = random.Random(seed)
    engines = rng.sample(range(4), rng.choice((1, 2, 2, 3, 4)))
    programs = [_program(rng, e, fifo) for e in sorted(engines)]
    tx = [[rng.randrange(1 << 32) for _ in range(rng.randrange(0, fifo + 1))] for _ in range(4)]
    return Case(seed, programs, tx, cycles, rng.randrange(1 << 30))


class Environment:
    """Random levels, loopbacks and SE0 episodes on undriven pads."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.levels = self.rng.randrange(256)
        self.loop = {pin: self.rng.randrange(8) for pin in range(8) if self.rng.random() < 0.35}
        self.toggle = [self.rng.choice((0.0, 0.002, 0.01, 0.05, 0.2)) for _ in range(8)]
        self.history: list[int] = []
        self.se0_until = -1

    def __call__(self, cycle: int, out: Outputs) -> int:
        rng = self.rng
        for pin in range(8):
            if rng.random() < self.toggle[pin]:
                self.levels ^= 1 << pin
        pads = pad_value(out, self.levels)
        self.history.append(pads)
        levels = self.levels
        for pin, source in self.loop.items():
            delayed = self.history[-3] if len(self.history) >= 3 else pads
            levels = (levels & ~(1 << pin)) | (delayed >> source & 1) << pin
        if cycle >= self.se0_until and rng.random() < 0.0015:
            self.se0_until = cycle + rng.randrange(20, 120)
            self.se0_pins = rng.sample(range(8), 2)
        if cycle < self.se0_until:
            for pin in self.se0_pins:
                levels &= ~(1 << pin)
        return pad_value(out, levels)


async def run_case(h: Harness, case: Case) -> None:
    await h.reset(2)
    h.pins = Environment(case.env_seed)
    rng = random.Random(case.seed ^ 0x5A5A)
    for p in case.programs:
        await h.load(p.engine, p.words, ownership=p.ownership)
        await h.command(SELECT, p.engine)
        for word in case.tx_words[p.engine]:
            if not await h.try_write(2, word, max_wait=50):
                break
    mask = sum(1 << p.engine for p in case.programs)
    await h.command(START, mask)
    end = h.cycle + case.cycles
    engines = [p.engine for p in case.programs]
    while h.cycle < end:
        r = rng.random()
        e = rng.choice(engines)
        if r < 0.25:
            await h.command(SELECT, e)
            await h.try_write(2, rng.randrange(1 << 32), max_wait=30)
        elif r < 0.5:
            await h.command(SELECT, e)
            await h.try_read(3, max_wait=30)
        elif r < 0.52:
            await h.command(SELECT, e)
            await h.status(rng.choice((0, 2, 3, 6, 7)))
        elif r < 0.53:  # restart an engine (STOP, clear a fault, START)
            await h.command(STOP, 1 << e)
            await h.command(CLEAR, 1 << e)
            await h.command(START, 1 << e)
        else:
            await h.idle(rng.randrange(20, 200))
    await h.command(STOP, mask)
    await h.idle(4)
