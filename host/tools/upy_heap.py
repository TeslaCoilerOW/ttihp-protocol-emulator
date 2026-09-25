#!/usr/bin/env python3
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Smallest MicroPython heap that runs the self-test and the flagship scenario.

Records each run on the reference model (CPython), then bisects the unix
port's ``-X heapsize`` for the MicroPython replay with the mpy-cross-compiled
package. The replay keeps the recorded trace (6 bytes per cycle) on the heap,
which a board does not need, so the report lists the trace size separately.
It is an estimate for boards: the rp2 port's object sizes match the unix
port's only approximately, and the ttboard SDK needs heap of its own.

    PE_HOST_MICROPYTHON=... PE_HOST_MPY_CROSS=... python3 host/tools/upy_heap.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOST = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOST / "tests"))
import support  # noqa: E402
import test_host_micropython as tm  # noqa: E402


def passes(heap_kib, package, kind, trace, want):
    proc = subprocess.run([tm.MICROPYTHON, "-X", "heapsize=%dK" % heap_kib,
                           str(HOST / "tests" / "upy" / "replay_main.py"), str(package), kind,
                           trace, str(support.SCENARIO)], capture_output=True, text=True,
                          timeout=600)
    return proc.returncode == 0 and proc.stdout.startswith(want)


def main():
    if not (tm.MICROPYTHON and tm.MPY_CROSS):
        print("needs PE_HOST_MICROPYTHON and PE_HOST_MPY_CROSS")
        return 2
    package = tm.compile_package(tempfile.mkdtemp())
    size = sum(p.stat().st_size for p in Path(package).rglob("*.mpy"))
    print("mpy-cross package (bytecode, host arch): %d bytes" % size)
    tmp = tempfile.mkdtemp()
    for kind in ("selftest", "flagship"):
        data, cycles, verdict = tm.record(kind)
        trace = os.path.join(tmp, kind + ".trace")
        Path(trace).write_bytes(data)
        want = "REPLAY_OK %s %d %s" % (kind, cycles, verdict)
        low, high = 8, 1024
        if not passes(high, package, kind, trace, want):
            print("%s: does not pass even with %d KiB" % (kind, high))
            continue
        while high - low > 1:
            mid = (low + high) // 2
            if passes(mid, package, kind, trace, want):
                high = mid
            else:
                low = mid
        print("%s: %d cycles, minimum heap %d KiB (includes the %d-byte replay trace)"
              % (kind, cycles, high, len(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
