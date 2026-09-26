#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Generate the demonstration firmware (docs/demo.md) with the committed assembler.

Every image in this directory is assembled by hardcaml/bin/assemble.ml from a
source file in this directory; nothing is hand-encoded. Two kinds of source:

* built-in examples re-parameterised for real parts (``--half-period``): the
  assembler emits the built-in source (``--source-output``), this script
  renames it and the image is assembled from that renamed source;
* short programs written here (timing probe, polling UART receiver, 32-bit
  SPI transaction, I2C ticker, hex formatter), emitted as strict
  ``protocol-emulator.firmware-source.v1`` JSON and assembled with
  ``--source``.

    python3 demo/firmware/build.py --assembler PATH/assemble.exe      # regenerate
    python3 demo/firmware/build.py --assembler PATH/assemble.exe --check

Without ``--assembler`` the script runs ``dune exec bin/assemble.exe`` in
``hardcaml/`` (``DUNE_BUILD_DIR`` selects the dune build directory).
``--check`` regenerates into a temporary directory and fails if any
committed file differs.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

ARCH = {
    "schema_version": "protocol-emulator.architecture.v1",
    "engine_count": 4,
    "data_width": 32,
    "program_words": 64,
    "fifo_words": 8,
    "issue": "fused",
    "prefetch": False,
}
CLOCK_HZ = 50_000_000
TMP102_READ = (0x48 << 1) | 1          # I2C address byte: 0x48, read


def I(mnemonic: str, **fields) -> dict:
    out = {"mnemonic": mnemonic}
    out.update(fields)
    return out


def source(name: str, engine: int, owned: int, instructions: list, notes: list,
           open_drain: int = 0) -> dict:
    return {
        "schema_version": "protocol-emulator.firmware-source.v1",
        "name": name,
        "architecture": ARCH,
        "engine": engine,
        "owned_pins": owned,
        "open_drain": open_drain,
        "clock_hz": CLOCK_HZ,
        "instructions": instructions,
        "notes": notes,
    }


def timing_probe() -> dict:
    return source("timing-probe", 3, 0xC0, [
        I("SET"),
        I("DIR", imm=0xC0),
        I("PINS", imm=6 | 7 << 3 | 7 << 6),            # clock 6, TX 7, RX 7
        I("SET", imm=0x40, label="frame"),
        I("WAIT", imm=10),
        I("SET"),
        I("WAIT", imm=20),
        I("COUNT", imm=2),
        I("SET", imm=0x80, label="burst"),
        I("WAIT", imm=3),
        I("SET"),
        I("WAIT", imm=5),
        I("LOOP", target="burst"),
        I("LOAD", a=0, imm=0xB4A5),
        I("SHL", a=0, c=16),
        I("XFER", a=16, b=7, c=12),                     # MSB first | drive; CPOL0/CPHA0; no sample
        I("SET"),
        I("WAIT", imm=40),
        I("JMP", target="frame"),
    ], [
        "Timing probe for the timing-isolation demonstration (docs/demo.md): engine 3 drives pins 6 and 7 push-pull in an endless 344-cycle frame.",
        "Frame: pin6 high 12 cycles, three pin7 pulses (5 high, 8 low), then a fused XFER of the constant 0xB4A5 (16 bits, MSB first, CPOL0/CPHA0, clock pin6 and data pin7, 7-cycle half period, no sampling).",
        "No PULL, PUSH, WAITPIN or WAITEVENT: the program never interacts with its FIFOs, mailbox or input pins, so the whole run stays inside the window of the formal timing-isolation property (formal/README.md).",
    ])


def uart_rx_poll(half: int, name: str) -> dict:
    """uart-rx with the bounded WAITPIN idle/start waits replaced by an
    unbounded polling loop (a real UART peer can stay idle indefinitely).
    Sample points match the built-in uart-rx: first data sample 3*half
    clocks after the start-bit detection edge; start detection has up to
    three extra clocks of polling latency."""
    return source(name, 1, 0x00, [
        I("COUNT", imm=7, label="next_byte"),
        I("LOAD", a=1, imm=0, label="idle"),
        I("IN", a=1, c=1),
        I("JZ", a=1, target="idle"),                    # line low: wait for idle high
        I("LOAD", a=1, imm=0, label="wait_start"),
        I("IN", a=1, c=1),
        I("JZ", a=1, target="start"),
        I("JMP", target="wait_start"),
        I("LOAD", a=1, imm=0, label="start"),
        I("WAIT", imm=3 * half - 4),
        I("IN", a=1, label="data"),
        I("WAIT", imm=2 * half - 3),
        I("LOOP", target="data"),
        I("SHR", a=1, c=24),
        I("MOV", a=3, b=1),
        I("LOAD", a=1, imm=0),
        I("IN", a=1, c=1),
        I("JZ", a=1, target="framing_error"),
        I("MOV", a=1, b=3),
        I("PUSH", a=1),
        I("JMP", target="next_byte"),
        I("FAULT", imm=64, label="framing_error"),
    ], [
        f"UART 8N1 RX on pin1, {2 * half} clocks per bit, low-byte RX words.",
        "Idle and start-bit waits poll the synchronized line with no bound (no WAITPIN, no LIMIT), so the line may stay idle indefinitely; start detection has up to three clocks of polling latency.",
        "Stop-bit low produces fault64; a full RX FIFO at frame delivery halts with fault4 (strict PUSH), as in the built-in uart-rx.",
    ])


def spi_xfer32(half: int) -> dict:
    return source("spi-xfer32", 2, 0x2C, [
        I("SET", imm=0x20),
        I("DIR", imm=0x2C),
        I("PINS", imm=2 | 3 << 3 | 4 << 6),             # SCK 2, MOSI 3, MISO 4
        I("PULL", label="next"),
        I("LOAD", a=1, imm=0),
        I("SET"),                                       # CS low
        I("XFER", a=32, b=half, c=28),                  # mode 0, MSB first, drive + sample
        I("SET", imm=0x20),                             # CS high
        I("PUSH"),
        I("JMP", target="next"),
    ], [
        f"SPI controller mode0, one 32-bit full-duplex transaction per TX word with CSn held low; pins SCK2/MOSI3/MISO4/CSn5, {half}-cycle half period.",
        "TX word 0x9F000000 reads a SPI flash JEDEC ID: the RX word's low three bytes are manufacturer, memory type and capacity.",
    ])


def i2c_ticker(name: str, period: int, address_byte: int) -> dict:
    assert period > 3
    return source(name, 1, 0x00, [
        I("LOAD", a=1, imm=address_byte),
        I("PUSH", label="tick"),
        I("WAIT", imm=period - 3),
        I("JMP", target="tick"),
    ], [
        f"Pushes the I2C address byte 0x{address_byte:02x} into its RX FIFO every {period} clocks (blocking PUSH, so a full queue pauses it); no pins.",
        "Used with a ROUTE from this engine to an i2c-read engine: a host-free periodic sensor read request.",
    ])


def hex_formatter() -> dict:
    def nibble(tag: str, high: bool) -> list:
        ops = [I("MOV", a=2, b=0)]                      # x = tx
        if high:
            ops.append(I("SHR", a=2, c=4))
        ops += [
            I("LOAD", a=3, imm=15),
            I("AND", a=2, b=3),                         # x = nibble
            I("MOV", a=1, b=2),                         # rx = nibble
            I("LOAD", a=3, imm=6),
            I("ADD", a=3, b=2),
            I("SHR", a=3, c=4),                         # y = 1 iff nibble >= 10
            I("JZ", a=3, target=tag),
            I("LOAD", a=3, imm=7),
            I("ADD", a=1, b=3),
            I("LOAD", a=3, imm=48, label=tag),
            I("ADD", a=1, b=3),                         # rx = ASCII hex digit
            I("PUSH"),
        ]
        return ops

    return source("hex-formatter", 2, 0x00,
                  [I("PULL", label="next")] + nibble("hi_digit", True) + nibble("lo_digit", False) + [
                      I("LOAD", a=1, imm=13),
                      I("PUSH"),
                      I("LOAD", a=1, imm=10),
                      I("PUSH"),
                      I("JMP", target="next"),
                  ], [
                      "Formats the low byte of each TX word as two upper-case ASCII hex digits followed by CR LF, pushed as four RX words; no pins.",
                      "Used between an i2c-read engine and a uart-tx engine with two ROUTEs, so readings appear as text on a serial terminal without host service.",
                  ])


def builtin(assembler: list, firmware: str, half: int, name: str, work: Path) -> dict:
    tmp_src = work / f"{firmware}.builtin.source.json"
    tmp_img = work / f"{firmware}.builtin.image.json"
    run(assembler + ["--firmware", firmware, "--half-period", str(half), "--clock-hz", str(CLOCK_HZ),
                     "--output", str(tmp_img), "--source-output", str(tmp_src)])
    data = json.loads(tmp_src.read_text())
    data["name"] = name
    data["notes"] = data["notes"] + [
        f"Built-in {firmware} with --half-period {half} (docs/demo.md), renamed {name}."]
    return data


def run(cmd: list, cwd: Path | None = None) -> None:
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        sys.exit(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stdout}")


def sources(assembler: list, work: Path) -> dict:
    out = {
        "timing-probe": timing_probe(),
        # 115200 baud at 50 MHz: 434 clocks per bit = 115,207 baud (+0.006 %).
        "uart-tx-115200": builtin(assembler, "uart-tx", 217, "uart-tx-115200", work),
        "uart-rx-poll-115200": uart_rx_poll(217, "uart-rx-poll-115200"),
        # Fast variant of the same receiver, 64 clocks per bit, for simulation.
        "uart-rx-poll-64": uart_rx_poll(32, "uart-rx-poll-64"),
        "spi-xfer32": spi_xfer32(16),
        # About 100 kHz SCL at 50 MHz (250-clock phases plus instruction overhead).
        "i2c-read-100k": builtin(assembler, "i2c-read", 250, "i2c-read-100k", work),
        # 10 readings per second at 50 MHz; the -sim variant every 40,000 clocks.
        "read-ticker": i2c_ticker("read-ticker", 5_000_000, TMP102_READ),
        "read-ticker-sim": i2c_ticker("read-ticker-sim", 40_000, TMP102_READ),
        "hex-formatter": hex_formatter(),
    }
    return out


def generate(assembler: list, out_dir: Path) -> list:
    written = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name, data in sources(assembler, work).items():
            src = out_dir / f"{name}.source.json"
            img = out_dir / f"{name}.image.json"
            src.write_text(json.dumps(data, indent=2) + "\n")
            run(assembler + ["--source", str(src), "--output", str(img)])
            written += [src.name, img.name]
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assembler", help="path to assemble.exe (default: dune exec in hardcaml/)")
    ap.add_argument("--check", action="store_true", help="regenerate into a temporary directory and compare")
    a = ap.parse_args()
    if a.assembler:
        assembler = [str(Path(a.assembler).resolve())]
    else:
        build_dir = os.environ.get("DUNE_BUILD_DIR")
        assembler = ["dune", "exec"] + (["--build-dir", build_dir] if build_dir else []) + \
            ["--root", str(REPO / "hardcaml"), "bin/assemble.exe", "--"]
    if not a.check:
        names = generate(assembler, HERE)
        print(f"wrote {len(names)} files in {HERE.relative_to(REPO)}")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        names = generate(assembler, Path(tmp))
        bad = [n for n in names if not (HERE / n).exists() or not filecmp.cmp(HERE / n, Path(tmp) / n, shallow=False)]
    if bad:
        print("differs from the committed files: " + ", ".join(bad))
        return 1
    print(f"{len(names)} files match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
