# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""MicroPython side of the differential replay of demo/pe_demo.py.

    micropython upy_replay.py HOST_DIR DEMO_DIR TRACE MODE CYCLES SEED FIRMWARE_DIR...

The CPython test (test_demo.py) runs pe_demo.isolation on the reference model
with the jumper environment and records every cycle (ModelPort
record_replay). Here the same scenario runs unchanged under MicroPython on
ReplayPort, which checks cycle by cycle that the code drives the recorded
ui byte and the recorded jumper drive. Prints REPLAY_OK and a digest of the
result.
"""

import sys

host_dir, demo_dir, trace, mode, cycles, seed = sys.argv[1:7]
firmware_dirs = sys.argv[7:]
sys.path.insert(0, host_dir)
sys.path.insert(0, demo_dir)

import pe_demo  # noqa: E402
from pe_host import ProtocolEmulator  # noqa: E402
from pe_host.peers import Environment  # noqa: E402
from pe_host.ports.replay import ReplayPort  # noqa: E402

with open(trace, "rb") as handle:
    data = handle.read()
port = ReplayPort(data, env=Environment([pe_demo.Jumpers()]))
chip = pe_demo.LibChip(ProtocolEmulator(port, timeout=200000))
images = pe_demo.lib_images(firmware_dirs, pe_demo.ISOLATION_IMAGES)
result = pe_demo.isolation(chip, images, mode, int(cycles), int(seed), log=lambda s: None)
if port.remaining:
    raise SystemExit("%d recorded cycles were not replayed" % port.remaining)
print("REPLAY_OK %d %s" % (port.count, pe_demo.digest(result)))
