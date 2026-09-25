# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Margin experiments: static protocol limits versus reference-model sweeps.

The schedule checks in ``pe_validate`` show that every edge happens when the
analyzer says. These experiments test the *protocol conclusions* drawn from
those schedules. For a reactive image the analyzer predicts the tightest
external timing the firmware tolerates (UART baud error, SPI target half
period / CS setup / CS high time, I2C target phase length). Each experiment
sweeps that external parameter with an independent wire-level peer against
the reference model and records which settings transfer correctly. The
prediction *agrees* when every setting the analyzer calls safe passes
(soundness); the report also gives the first setting that fails, so the
tightness of the bound is visible. External edges in the model are aligned to
clock edges, so the predictions below use the synchronous form of the latency
rules (no extra cycle of phase uncertainty).
"""

from __future__ import annotations

import random
from typing import Any

import pe_timing as T
from pe_timing import EK, EPC, EPIN, ET, INF


def _host(mods: dict):
    R, Hm = mods["model.reference"], mods["model.host"]
    model = R.Reference()
    return model, Hm.Host(model)


def _load(host: Any, prog: T.Program) -> None:
    host.load(prog.engine, prog.words, ownership=prog.ownership, open_drain=prog.open_drain)


def _read_all(host: Any, n: int) -> list[int] | None:
    got = []
    for _ in range(n):
        try:
            got.append(host.read(3, timeout=400))
        except TimeoutError:
            return None
    return got


def _arrive_after(an: T.Analysis, bpcs: set[int], targets: set[int]) -> int:
    """Largest offset from completing a boundary in ``bpcs`` to arriving at one in ``targets``."""
    q = an.query(lambda e: e[EK] == "arrive" and e[EPC] in targets)
    return int(max(q.from_anchor(n)[0] for n in an.iter_nodes() if n.bpc in bpcs))


def _first_after(an: T.Analysis, bpcs: set[int], pred) -> int:  # noqa: ANN001
    worst = -1
    for n in an.iter_nodes():
        if n.bpc in bpcs:
            for v in n.variants:
                ts = [e[ET] for e in v.events if pred(e)]
                if ts:
                    worst = max(worst, ts[0])
    return worst


# ----------------------------------------------------------------------------
# UART RX: baud tolerance
# ----------------------------------------------------------------------------

def uart_rx_prediction(an: T.Analysis, bit: int, n_frames: int, back_to_back: bool) -> bool:
    """True if every sample of every frame lands inside its bit for all data."""
    p = an.p
    start_pcs = {i.pc for i in p.ins if i.op == T.WAITPIN and i.a == 1 and i.b == 0}
    idle_pcs = {i.pc for i in p.ins if i.op == T.WAITPIN and i.a == 1 and i.b == 1}
    nodes = [n for n in an.iter_nodes() if n.bpc in start_pcs]
    samples = sorted({tuple(e[ET] for e in v.events if e[EK] == "sample") for n in nodes for v in n.variants
                      if v.kind == "boundary"})[0]
    idle_arrive = _arrive_after(an, start_pcs, idle_pcs)
    arm = _arrive_after(an, start_pcs, start_pcs)
    lateness = 0
    for _ in range(n_frames):
        for m, d in enumerate(samples, start=1):
            pos = lateness + d
            if m <= 8 and not (m * bit <= pos <= (m + 1) * bit - 1):
                return False
            if m == 9 and not (9 * bit <= pos <= (10 * bit - 1 if back_to_back else 10 ** 9)):
                return False
        if back_to_back:
            if lateness + idle_arrive > 10 * bit - 1:
                return False
            lateness = max(0, lateness + arm - 10 * bit)
    return True


def uart_rx_sweep(mods: dict, validator: Any, tracer: Any, seed: int) -> dict[str, Any]:
    prog = validator.programs["uart-rx"]
    an = T.Analysis(prog)
    patterns = [[0x55, 0xAA, 0x00, 0xFF, 0x7F, 0x80, 0x01, 0xFE],
                [0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55]]
    rng = random.Random(seed)
    patterns.append([rng.randrange(256) for _ in range(8)])
    rows = []
    agrees = True
    for back_to_back in (True, False):
        for bit in range(56, 73):
            predicted = uart_rx_prediction(an, bit, 8, back_to_back)
            observed = True
            for words in patterns:
                model, host = _host(mods)
                gap = 0 if back_to_back else 3 * bit
                frame = 10 * bit + gap
                t0 = {"start": None}

                def pins(cycle: int, words=words, bit=bit, frame=frame, t0=t0) -> int:
                    level = 1
                    if t0["start"] is not None and cycle >= t0["start"]:
                        f, off = divmod(cycle - t0["start"], frame)
                        if f < len(words):
                            i = off // bit
                            level = 0 if i == 0 else (words[f] >> (i - 1) & 1 if i <= 8 else 1)
                    return 0xFF & ~2 | level << 1

                tracer.label = f"margins:uart-rx:{bit}:{int(back_to_back)}"
                host.pins = pins
                host.cycle(reset=True)
                _load(host, prog)
                host.command(0, prog.engine)
                host.command(4, 1 << prog.engine)
                t0["start"] = len(host.cycles) + 30
                host.idle(30 + len(words) * frame + 2 * bit)
                engine = model.engines[prog.engine]
                ok = engine.fault in (0, 3) and [w & 0xFF for w in engine.rx] == words
                observed = observed and ok
                tracer.flush()
            rows.append({"bit_cycles": bit, "error_pct": round((bit / 64 - 1) * 100, 3),
                         "back_to_back": back_to_back, "predicted_safe": predicted, "observed_pass": observed})
            if predicted and not observed:
                agrees = False
    safe = [r["bit_cycles"] for r in rows if r["predicted_safe"] and r["back_to_back"]]
    seen = [r["bit_cycles"] for r in rows if r["observed_pass"] and r["back_to_back"]]
    exact = all(r["predicted_safe"] == r["observed_pass"] for r in rows)
    return {"agrees": agrees, "exact": exact,
            "summary": f"back-to-back 8N1 at 64 cycles/bit: predicted safe bit periods {min(safe)}..{max(safe)}, "
                       f"observed passing {min(seen)}..{max(seen)} (patterns incl. 0x55/0xAA); "
                       f"prediction {'matches every setting' if exact else 'is sound but conservative'}",
            "rows": rows}


# ----------------------------------------------------------------------------
# SPI target: half period, CS setup and CS high time
# ----------------------------------------------------------------------------

def spi_target_prediction(an: T.Analysis, mode: int) -> dict[str, int]:
    p = an.p
    cpol, cpha = mode >> 1, mode & 1
    lead, trail = 1 - cpol, cpol

    def w(pin: int, level: int) -> set[int]:
        return {i.pc for i in p.ins if i.op == T.WAITPIN and i.a == pin and i.b == level}

    w_lead, w_trail, w_cs0, w_cs1 = w(2, lead), w(2, trail), w(5, 0), w(5, 1)
    miso_out = (lambda e: e[EK] == "pad" and e[EPIN] == 4 and e[6] == "OUT")
    mosi_in = (lambda e: e[EK] == "sample" and e[EPIN] == 3)
    launch = w_trail if cpha == 0 else w_lead
    sample = w_lead if cpha == 0 else w_trail
    k_out = _first_after(an, launch, miso_out)
    k_in = _first_after(an, sample, mosi_in)
    rearm = max(_arrive_after(an, w_lead, w_trail), _arrive_after(an, w_trail, w_lead | w_cs1))
    h_min = max(k_out + 3, k_in + 1, rearm)
    first = _arrive_after(an, w_cs0, w_lead)
    s_min = max(first, (_first_after(an, w_cs0, miso_out) + 3) if cpha == 0 else 0)
    # CS must still read high when the firmware re-checks it after the frame
    second_cs1 = {pc for pc in w_cs1 if pc < min(w_cs0)}
    c_min = _arrive_after(an, {pc for pc in w_cs1 if pc > min(w_cs0)}, second_cs1) + 1
    return {"half_period": h_min, "cs_setup": s_min, "cs_high": c_min}


def _spi_trial(mods: dict, prog: T.Program, mode: int, H: int, S: int, C: int, rng: random.Random,
               tracer: Any) -> bool:
    model, host = _host(mods)
    cpol, cpha = mode >> 1, mode & 1
    n = 4
    mosi_bytes = [rng.randrange(256) for _ in range(n)]
    miso_bytes = [rng.randrange(256) for _ in range(n)]
    events: list[tuple[int, int, int, int]] = []
    samples: dict[int, tuple[int, int]] = {}
    t = 0
    for f, byte in enumerate(mosi_bytes):
        bits = [(byte >> (7 - i)) & 1 for i in range(8)]
        mosi = bits[0] if cpha == 0 else 1
        events.append((t, 0, cpol, mosi))
        for i in range(16):
            te = t + S + i * H
            leading = i % 2 == 0
            j = i // 2
            if cpha == 0:
                if not leading and j + 1 < 8:
                    mosi = bits[j + 1]
                if leading:
                    samples[te] = (f, j)
            else:
                if leading:
                    mosi = bits[j]
                else:
                    samples[te] = (f, j)
            events.append((te, 0, (1 - cpol) if leading else cpol, mosi))
        t_end = t + S + 15 * H + H
        events.append((t_end, 1, cpol, mosi))
        t = t_end + C
    horizon = t + 50
    got = [0] * n
    state = {"start": None, "i": 0, "cur": (1, cpol, 1)}

    def pins(cycle: int) -> int:
        out = model.outputs()
        miso = (out.uio_out >> 4 & 1) if out.uio_oe >> 4 & 1 else 1
        if state["start"] is not None:
            rel = cycle - state["start"]
            while state["i"] < len(events) and events[state["i"]][0] <= rel:
                _, cs, sck, mosi = events[state["i"]]
                state["cur"] = (cs, sck, mosi)
                state["i"] += 1
            if rel in samples:
                f, j = samples[rel]
                got[f] |= miso << (7 - j)
        cs, sck, mosi = state["cur"]
        return (0xFF & ~(4 | 8 | 16 | 32)) | sck << 2 | mosi << 3 | miso << 4 | cs << 5

    host.pins = pins
    host.cycle(reset=True)
    _load(host, prog)
    host.command(0, prog.engine)
    for b in miso_bytes:
        host.write(2, b)
    host.command(4, 1 << prog.engine)
    state["start"] = len(host.cycles) + 40
    host.idle(40 + horizon)
    engine = model.engines[prog.engine]
    rx = [w & 0xFF for w in engine.rx]
    ok = engine.fault == 0 and rx == mosi_bytes and got == miso_bytes
    tracer.flush()
    return ok


def spi_target_sweep(mods: dict, validator: Any, tracer: Any, seed: int) -> dict[str, Any]:
    out_rows = []
    agrees = True
    notes = []
    for mode in range(4):
        prog = validator.programs[f"spi-target-mode{mode}"]
        an = T.Analysis(prog)
        pred = spi_target_prediction(an, mode)
        rng = random.Random(f"{seed}:spi:{mode}")
        for param, values in (("half_period", range(1, 11)), ("cs_setup", range(1, 11)),
                              ("cs_high", range(1, 15))):
            first_pass = None
            for x in values:
                H, S, C = 16, 16, 32
                if param == "half_period":
                    H = x
                elif param == "cs_setup":
                    S = x
                else:
                    C = x
                tracer.label = f"margins:spi-target{mode}:{param}={x}"
                ok = all(_spi_trial(mods, prog, mode, H, S, C, rng, tracer) for _ in range(3))
                safe = x >= pred[param]
                out_rows.append({"mode": mode, "param": param, "value": x, "predicted_safe": safe,
                                 "observed_pass": ok})
                if safe and not ok:
                    agrees = False
                if ok and first_pass is None:
                    first_pass = x
            notes.append(f"mode{mode} {param}: predicted >= {pred[param]}, observed first pass {first_pass}")
    exact = all(r["predicted_safe"] == r["observed_pass"] for r in out_rows)
    return {"agrees": agrees, "exact": exact, "summary": "; ".join(notes), "rows": out_rows}


# ----------------------------------------------------------------------------
# I2C target: phase length
# ----------------------------------------------------------------------------

def i2c_target_prediction(an: T.Analysis) -> int:
    p = an.p
    scl, sda = 6, 7

    def w(pin: int, level: int) -> set[int]:
        return {i.pc for i in p.ins if i.op == T.WAITPIN and i.a == pin and i.b == level}

    w_low, w_high = w(scl, 0), w(scl, 1)
    sda_drive = (lambda e: e[EK] == "pad" and e[EPIN] == sda and not e[6].startswith("fault"))
    scl_grab = (lambda e: e[EK] == "pad" and e[EPIN] == scl and e[5] == T.P0)
    need = 0
    for n in an.iter_nodes():
        if n.bpc not in w_low:
            continue
        for v in n.variants:
            for e in v.events:
                if scl_grab(e):          # target stretches: timing below is no longer the controller's
                    need = max(need, e[ET] + 3)
                    break
                if sda_drive(e):
                    need = max(need, e[ET] + 3)
    samp = _first_after(an, w_high, lambda e: e[EK] == "sample" and e[EPIN] == sda)
    # re-arm: the next pin wait must be reached before the controller's next edge,
    # unless the target holds SCL low (stretch) in between
    waits = {i.pc for i in p.ins if i.op == T.WAITPIN and i.a in (scl, sda)}
    rearm = 0
    for n in an.iter_nodes():
        if n.bpc not in waits:
            continue
        for v in n.variants:
            for e in v.events:
                if scl_grab(e):
                    break
                if e[EK] == "arrive" and e[EPC] in waits:
                    rearm = max(rearm, e[ET])
                    break
    return max(need, samp + 1, rearm)


def _i2c_trial(mods: dict, prog: T.Program, read: bool, P: int, rng: random.Random, tracer: Any, *,
               transactions: int = 1, controller_ack: bool = False, wrong_last_address: bool = False) -> bool:
    """One or more START..STOP transactions from a bit-banged controller honouring stretching.

    Data changes on the same edge as SCL falls (hold 0) and the controller samples SDA
    on the first high cycle of a clock, both adversarial choices."""
    model, host = _host(mods)
    data = [rng.randrange(256) for _ in range(transactions)]
    segments: list[tuple[int, int, int]] = []
    reads: list[int] = []       # indices of segments where the controller samples SDA

    def send_byte(value: int) -> None:
        for bit in (value >> i & 1 for i in range(7, -1, -1)):
            segments.extend([(0, bit, P), (1, bit, P)])
        segments.append((0, 1, P))
        reads.append(len(segments))
        segments.append((1, 1, P))            # ACK clock: controller samples SDA

    for k in range(transactions):
        segments.extend([(1, 1, P), (1, 0, P)])  # START
        address = 0x85 if read else 0x84
        if wrong_last_address and k == transactions - 1:
            address ^= 0x02                   # 7-bit address 0x43 instead of 0x42
        send_byte(address)
        if read:
            for _ in range(8):
                segments.append((0, 1, P))
                reads.append(len(segments))
                segments.append((1, 1, P))
            ack = 0 if controller_ack else 1
            segments.extend([(0, ack, P), (1, ack, P)])  # controller NACK (or ACK)
        else:
            send_byte(data[k])
        segments.extend([(0, 0, P), (1, 0, P), (1, 1, P)])  # STOP
        segments.append((1, 1, 4 * P))                      # bus free
    st = {"start": None, "pos": 0, "elapsed": 0, "sampled": []}

    def pins(cycle: int) -> int:
        out = model.outputs()
        active = st["start"] is not None and cycle >= st["start"] and st["pos"] < len(segments)
        clock, sda = segments[st["pos"]][:2] if active else (1, 1)
        clock &= int(not (out.uio_oe & 64))
        sda &= int(not (out.uio_oe & 128))
        if active:
            seg = segments[st["pos"]]
            if seg[0] and not clock:
                pass                           # target stretches SCL: hold time
            else:
                if st["elapsed"] == 0 and st["pos"] in reads:
                    st["sampled"].append(sda)  # sample at the first high tick (adversarial)
                st["elapsed"] += 1
                if st["elapsed"] == seg[2]:
                    st["pos"] += 1
                    st["elapsed"] = 0
        return (0xFF & 63) | clock << 6 | sda << 7

    host.pins = pins
    host.cycle(reset=True)
    _load(host, prog)
    host.command(0, prog.engine)
    if read:
        for d in data:
            host.write(2, d)
    host.command(4, 1 << prog.engine)
    st["start"] = len(host.cycles) + 20
    for _ in range(transactions * (40 * P * 30 + 2000)):
        host.cycle()
        if st["pos"] >= len(segments):
            break
    host.idle(20)
    engine = model.engines[prog.engine]
    s = st["sampled"]
    tracer.flush()
    if controller_ack:
        return engine.fault == 67
    if wrong_last_address:
        return engine.fault == 66
    if read:
        if len(s) != 9 * transactions:
            return False
        for k in range(transactions):
            frame = s[9 * k:9 * k + 9]
            if frame[0] != 0 or sum(b << (7 - i) for i, b in enumerate(frame[1:])) != data[k]:
                return False
        return engine.fault == 0
    return engine.fault == 0 and s == [0, 0] * transactions and [w & 0xFF for w in engine.rx] == data


def i2c_target_sweep(mods: dict, validator: Any, tracer: Any, seed: int) -> dict[str, Any]:
    rows = []
    agrees = True
    notes = []
    for name in ("i2c-target-write", "i2c-target-read"):
        prog = validator.programs[name]
        an = T.Analysis(prog)
        pred = i2c_target_prediction(an)
        rng = random.Random(f"{seed}:{name}")
        first_pass = None
        for P in range(2, 21):
            tracer.label = f"margins:{name}:P={P}"
            ok = all(_i2c_trial(mods, prog, name.endswith("read"), P, rng, tracer) for _ in range(3))
            safe = P >= pred
            rows.append({"image": name, "phase": P, "predicted_safe": safe, "observed_pass": ok})
            if safe and not ok:
                agrees = False
            if ok and first_pass is None:
                first_pass = P
        declared = 16
        notes.append(f"{name}: predicted phase >= {pred} cycles (declared >= {declared}), observed first pass {first_pass}")
        # coverage trials at the declared phase: back-to-back transactions, and (read) a controller
        # that ACKs the data byte, which the firmware must reject with fault 67
        tracer.label = f"margins:{name}:two-transactions"
        two = _i2c_trial(mods, prog, name.endswith("read"), declared, rng, tracer, transactions=2)
        rows.append({"image": name, "phase": declared, "trial": "two transactions", "predicted_safe": True,
                     "observed_pass": two})
        agrees = agrees and two
        tracer.label = f"margins:{name}:wrong-second-address"
        wrong = _i2c_trial(mods, prog, name.endswith("read"), declared, rng, tracer, transactions=2,
                           wrong_last_address=True)
        rows.append({"image": name, "phase": declared, "trial": "second address wrong -> fault66",
                     "predicted_safe": True, "observed_pass": wrong})
        agrees = agrees and wrong
        notes.append(f"{name}: mismatched second address {'faults 66 as documented' if wrong else 'NOT rejected'}")
        if name.endswith("read"):
            tracer.label = f"margins:{name}:controller-ack"
            ack = _i2c_trial(mods, prog, True, declared, rng, tracer, controller_ack=True)
            rows.append({"image": name, "phase": declared, "trial": "controller ACK -> fault67",
                         "predicted_safe": True, "observed_pass": ack})
            agrees = agrees and ack
            notes.append(f"{name}: two transactions {'pass' if two else 'FAIL'}, controller ACK "
                         f"{'faults 67 as documented' if ack else 'NOT rejected'}")
        else:
            notes.append(f"{name}: two transactions {'pass' if two else 'FAIL'}")
    exact = all(r["predicted_safe"] == r["observed_pass"] for r in rows)
    return {"agrees": agrees, "exact": exact, "summary": "; ".join(notes), "rows": rows}


def run_all(mods: dict, validator: Any, tracer: Any, seed: int) -> dict[str, Any]:
    return {"uart-rx-baud": uart_rx_sweep(mods, validator, tracer, seed),
            "spi-target-timing": spi_target_sweep(mods, validator, tracer, seed),
            "i2c-target-phase": i2c_target_sweep(mods, validator, tracer, seed)}
