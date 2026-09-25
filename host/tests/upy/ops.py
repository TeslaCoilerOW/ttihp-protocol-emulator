# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Interpreter for host-operation programs (lists of JSON-able ops), shared by
the CPython tests, the randomized campaign (tools/fuzz_host.py) and the
MicroPython replay. MicroPython compatible.

Ops mirror the test/harness.py host driver one to one:
  ["reset", n]                     harness start()/reset(n)
  ["deselect", n]                  harness reset(n, deselect=True)
  ["load", engine, words, own, od] harness load()
  ["command", op, payload]         harness command()
  ["write", window, word]          harness write()
  ["try_write", window, word, max_wait, nibbles]
  ["status", index]                harness status()
  ["read", window, pauses|None]    harness read()
  ["try_read", window, max_wait, nibbles]
  ["idle", n] / ["bounce"]
  ["firmware", name]               harness load_firmware()
"""


def run_ops(pe, ops, firmware_dir=None, hooks=None):
    """Run ops on a ProtocolEmulator (strict=False); returns [kind, value]
    pairs for every op that produces a value (command: True/False/None).
    hooks: optional object with before(index, op) and after(index, op,
    results), e.g. tests/acceptance.py's AcceptanceOracle."""
    out = []
    for index, op in enumerate(ops):
        kind = op[0]
        results = []
        if hooks is not None:
            hooks.before(index, op)
        if kind == "reset":
            pe.reset(op[1])
        elif kind == "deselect":
            pe.deselect(op[1])
        elif kind == "load":
            pe.load_program(op[1], op[2], op[3], op[4])
        elif kind == "command":
            results.append(pe.command(op[1], op[2]))
        elif kind == "write":
            pe.write_word(op[1], op[2])
        elif kind == "try_write":
            results.append(pe.try_write_word(op[1], op[2], op[3], op[4]))
        elif kind == "status":
            results.append(pe.read_status(op[1]))
        elif kind == "read":
            results.append(pe.read_word(op[1], None, op[2]))
        elif kind == "try_read":
            results.append(pe.try_read_word(op[1], op[2], op[3]))
        elif kind == "idle":
            pe.idle(op[1])
        elif kind == "bounce":
            pe.bounce()
        elif kind == "firmware":
            from pe_host.image import FirmwareImage, join
            pe.load_image(FirmwareImage.load(join(firmware_dir, op[1] + ".image.json")))
        else:
            raise ValueError("unknown op " + str(kind))
        if hooks is not None:
            hooks.after(index, op, results)
        for value in results:
            out.append([kind, value])
    return out


def digest(pairs):
    """Interpreter-independent digest of run_ops results."""
    import binascii
    import hashlib
    return binascii.hexlify(hashlib.sha256(repr(pairs).encode()).digest()).decode()[:16]
