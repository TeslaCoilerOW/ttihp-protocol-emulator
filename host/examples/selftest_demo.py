# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Bring-up self-test, the same script on every backend (docs/host.md).

CPython (reference model):   python3 host/examples/selftest_demo.py
Demo board / Pico:           mpremote run host/examples/selftest_demo.py
                             (after copying pe_host/ to the board)
Nothing needs to be attached; the "trigger" section drives uio0 for a few
cycles.
"""

import sys

if sys.implementation.name != "micropython":
    _here = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
    sys.path.insert(0, _here + "/..")

import pe_host  # noqa: E402
from pe_host import selftest  # noqa: E402


def main(backend=None, port_options=None):
    pe = pe_host.connect(backend, port_options=port_options)
    print("backend:", pe.port.name, "fast" if getattr(pe.port, "fast", False) else "")
    result = selftest.run(pe, log=print)
    print(result.summary())
    return result


if __name__ == "__main__":
    main()
