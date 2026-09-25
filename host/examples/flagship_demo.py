# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""The flagship scenario (firmware/flagship-scenario.json), same script everywhere.

Software peers (default): the host plays the UART sender and receiver, the
SPI target and the I2C target on the uio pins in lockstep with the clock it
supplies, so nothing has to be attached to the chip or FPGA. On MicroPython
the RP2's internal pull-ups serve SCL/SDA (uio6/uio7).

    python3 host/examples/flagship_demo.py                 # reference model
    mpremote run host/examples/flagship_demo.py            # demo board / Pico
      (after copying pe_host/ and firmware/ to the board, see docs/host.md)

With real peripherals instead: main(peers="external", free_run_hz=...).
"""

import sys

SCENARIO = "firmware/flagship-scenario.json"
if sys.implementation.name != "micropython":
    _here = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
    sys.path.insert(0, _here + "/..")
    SCENARIO = _here + "/../../firmware/flagship-scenario.json"

import pe_host  # noqa: E402
from pe_host.flagship import run_flagship  # noqa: E402


def main(backend=None, scenario=SCENARIO, peers="software", free_run_hz=None,
         port_options=None):
    if port_options is None:
        port_options = {}
        if sys.implementation.name == "micropython" and peers == "software":
            port_options = {"pullups": 0xC0}
    pe = pe_host.connect(backend, port_options=port_options)
    print("backend:", pe.port.name)
    return run_flagship(pe, scenario, peers=peers, free_run_hz=free_run_hz, log=print)


if __name__ == "__main__":
    main()
