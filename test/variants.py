# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
"""Named design variants for the cocotb suite (PE_VARIANT).

``base`` is the design of record (``src/protocol_emulator_core.v`` from
``configs/instruction-sram-32.json``) and is what the Tiny Tapeout CI runs. The
other names are the area/reset variants of ``docs/area-study.md`` sections 4, 5
and 8, defined by ``configs/variants/<name>.json`` (a refinement config with an
optional ``"options"`` object, see ``model/variant.py``).

``SPEC`` restates the variant definitions independently of the config files;
``resolve`` loads the config file when it exists and fails if it disagrees
with ``SPEC``, so a mislabelled config or core cannot silently pass as another
variant.

Environment (the Makefile sets these from ``PE_VARIANT``):
  PE_VARIANT   variant name (default ``base``)
  PE_CONFIG    config file (default: configs/instruction-sram-32.json for base,
               configs/variants/<name>.json otherwise; SPEC when that file is absent)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from model.reference import Config
from model.variant import Options, VariantConfig, config_from_json

REPO = Path(__file__).resolve().parent.parent
BASE_CONFIG = REPO / "configs" / "instruction-sram-32.json"
VARIANT_CONFIGS = REPO / "configs" / "variants"

_CN = {"fifo_storage_reset": True, "narrow_image_regs": True}
_DIET = {"reset": "async_sync_release", **_CN, "debug_counters": False, "pc_bits": "saturating_7",
         "shift": "byte_lane"}
# Timing options (docs/timing-closure.md); they do not change pin behaviour.
_TIMING = {"host_nibble_slots": True, "split_command_decode": True, "split_engine_issue": True,
           "split_instruction_decode": True, "keep_counter_increments": True,
           "fifo_write_staging": True, "clear_outputs_only": True}

# name -> (fifo_words, options); every variant is the SRAM-32 base architecture otherwise.
SPEC: dict[str, tuple[int, dict[str, Any]]] = {
    "base": (8, {}),
    "rstreg": (8, {"reset": "sync_registered"}),
    "cn": (8, {"reset": "async", **_CN}),
    "cn_s2": (8, {"reset": "async_sync_release", **_CN}),
    "diet4": (4, _DIET),
    "diet2": (2, _DIET),
    "rstreg_timing": (8, {"reset": "sync_registered", "narrow_image_regs": True, **_TIMING}),
    "cn_s2_timing": (8, {"reset": "async_sync_release", **_CN, **_TIMING}),
}


def name() -> str:
    return os.environ.get("PE_VARIANT", "") or "base"


def spec_config(variant: str) -> VariantConfig:
    if variant not in SPEC:
        raise ValueError(f"unknown PE_VARIANT {variant!r} (known: {', '.join(SPEC)})")
    fifo_words, options = SPEC[variant]
    return VariantConfig(fifo_words=fifo_words, options=Options(**options))


def config_path(variant: str) -> Path:
    explicit = os.environ.get("PE_CONFIG")
    if explicit:
        return Path(explicit)
    return BASE_CONFIG if variant == "base" else VARIANT_CONFIGS / f"{variant}.json"


def resolve(variant: str | None = None) -> tuple[VariantConfig, str]:
    """(configuration, provenance) of the variant under test."""
    variant = variant or name()
    path = config_path(variant)
    if variant == "base" and os.environ.get("PE_CONFIG"):
        # As before variants existed: PE_CONFIG alone selects another architecture.
        return config_from_json(json.loads(path.read_text())), str(path)
    if variant not in SPEC:
        if not path.exists():
            raise ValueError(f"unknown PE_VARIANT {variant!r} and no config {path}")
        return config_from_json(json.loads(path.read_text())), str(path)
    expected = spec_config(variant)
    if not path.exists():
        if os.environ.get("PE_CONFIG"):
            raise FileNotFoundError(path)
        return expected, f"SPEC[{variant}] ({path.name} not present)"
    loaded = config_from_json(json.loads(path.read_text()))
    if loaded != expected:
        raise ValueError(f"{path} does not match the {variant!r} variant definition:\n"
                         f"  config: {loaded}\n  spec:   {expected}")
    return loaded, str(path)


def design_config() -> Config | VariantConfig:
    """Model configuration of the design under test.

    The base variant returns a plain ``Config`` so the verbatim reference model
    runs exactly as before; every other variant returns a ``VariantConfig``.
    """
    config, _ = resolve()
    if config.options.is_base and config.fifo_words in (8, 32):
        return config.base
    return config


def options(config: Config) -> Options:
    return config.options if isinstance(config, VariantConfig) else Options()
