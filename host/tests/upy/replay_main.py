# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""MicroPython side of the differential replay (tests/test_host_micropython.py).

    micropython replay_main.py HOST_DIR KIND TRACE SCENARIO
    micropython replay_main.py HOST_DIR ops TRACE OPS_JSON FIRMWARE_DIR

KIND is "selftest", "flagship[:STRETCH]", "external" (the flagship scenario
with peers="external" and 3,000 free-run cycles) or "ops" (a host-operation
program, tests/upy/ops.py). The host library runs unchanged
on ReplayPort: every cycle it must drive the ui byte and (for the software
peers) the uio drive recorded by the CPython model run, and it sees the
recorded uo samples. Prints REPLAY_OK with the cycle count and verdict.
"""

import gc
import sys

host_dir, kind, trace_path, scenario_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, host_dir)

import pe_host.host  # noqa: E402
from pe_host import ProtocolEmulator, selftest  # noqa: E402
from pe_host.errors import ReplayDivergence  # noqa: E402
from pe_host.flagship import run_flagship  # noqa: E402
from pe_host.ports.replay import ReplayPort  # noqa: E402

with open(trace_path, "rb") as handle:
    data = handle.read()
gc.collect()
before = gc.mem_alloc()
port = ReplayPort(data)
pe = ProtocolEmulator(port)
if kind == "ops":
    import json
    from ops import digest, run_ops
    with open(scenario_path) as handle:
        program = json.load(handle)
    pe.strict = False
    pairs = run_ops(pe, program, sys.argv[5])
    verdict = digest(pairs)
    summary = "%d ops, %d values" % (len(program), len(pairs))
elif kind == "selftest":
    result = selftest.run(pe)
    verdict = "%s %d" % (result.passed, len(result.checks))
    summary = result.summary()
elif kind == "external":
    result = run_flagship(pe, scenario_path, peers="external", free_run_cycles=3000)
    verdict = "%s host_fault=%s faults=%s" % (
        result.passed, result.host_fault,
        ",".join("%d:%d" % (s.engine, s.fault) for s in result.fault_report.faulted))
    summary = result.summary()
else:
    stretch = int(kind.split(":")[1]) if ":" in kind else 0
    result = run_flagship(pe, scenario_path, i2c_stretch=stretch)
    verdict = "%s %s" % (result.passed, " ".join("%02x" % w for w in result.engine2_rx_words))
    summary = result.summary()
if port.remaining:
    raise ReplayDivergence("%d recorded cycles were not replayed" % port.remaining)
gc.collect()
print("REPLAY_OK", kind, port.count, verdict)
print("HEAP_IN_USE_AFTER", gc.mem_alloc() - before)
print("IMPORTED_FROM", getattr(pe_host.host, "__file__", "?"))
print(summary)
