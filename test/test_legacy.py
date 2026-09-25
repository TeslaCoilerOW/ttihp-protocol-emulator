# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Replay of the monorepo's differential workloads (model/verification.py).

Each workload was written against the reference model with its own wire peers
and assertions (queues, DMA congestion, SPI mode/phase matrix, strict RX push,
input triggers, timeouts, JTAG, waveform, I2C target, UART overflow, ...). The
recorded per-cycle stimulus (ui_in, uio_in, reset, ena) is replayed here; the
harness checks the DUT against a fresh reference model on every cycle, and the
recorded post-edge outputs must match as well.
"""

from __future__ import annotations

import os

import cocotb

import scenarios
from harness import CocotbHarness, design_config, load_image
from model.variant import VariantConfig
from model.verification import FIRMWARE_SCENARIOS, SCENARIOS, differential_host
from variant_workloads import record, substitute

ALL = SCENARIOS + FIRMWARE_SCENARIOS
# Gate-level runs are much slower; replay a representative subset there
# unless PE_LEGACY=all.
GL_SUBSET = ("queues", "dma", "pins_events", "timeout_atomic", "strict_push", "input_triggers",
             "firmware_i2c_read", "firmware_spi_target")


def selected() -> list[str]:
    choice = os.environ.get("PE_LEGACY", "")
    if choice == "all" or (not choice and not os.environ.get("PE_GATE_LEVEL")):
        return list(ALL)
    if not choice:
        return list(GL_SUBSET)
    return [name for name in choice.split(",") if name]


async def replay(dut, scenario: str) -> None:
    """Replay one monorepo differential workload in lockstep.

    Design variants (PE_VARIANT) record the workload against the variant model
    (variant_workloads.py); the few that state a base-only fact run an adapted
    recording or a live substitute scenario, and the log says which.
    """
    config = design_config()
    if isinstance(config, VariantConfig):
        replacement = substitute(scenario, config)
        if replacement is not None:
            function, kwargs = replacement
            dut._log.info("%s: variant substitute scenarios.%s(%s)", scenario, function, kwargs)
            h = CocotbHarness(dut)
            await getattr(scenarios, function)(h, **kwargs)
            return
        host = record(scenario, config, load_image)  # the variant's image set (harness.image_dir)
    else:
        host = differential_host(scenario)  # runs the workload's own model-level assertions
    h = CocotbHarness(dut)
    await h.start(reset_cycles=1)
    for index, cycle in enumerate(host.cycles):
        await h.step(cycle.ui, reset=cycle.reset, enabled=cycle.enabled, pins=cycle.pins)
        assert h.expected == cycle.expected, f"{scenario}: model replay diverged from recording at cycle {index}"
    await h.step()  # final post-edge comparison
    dut._log.info("%s: %d cycles in lockstep", scenario, len(host.cycles))


def _register(scenario: str) -> None:
    async def run(dut) -> None:
        await replay(dut, scenario)

    name = f"test_legacy_{scenario}"
    run.__name__ = run.__qualname__ = name
    run.__doc__ = f"Lockstep replay of the monorepo '{scenario}' differential workload."
    # Unselected workloads (PE_LEGACY, gate-level subset) are reported as skipped.
    globals()[name] = cocotb.test(skip=scenario not in selected())(run)


for _scenario in ALL:
    _register(_scenario)
