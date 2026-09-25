#!/usr/bin/env python3
"""Derive the .sby files of a design variant (formal/run.sh --variant).

usage: variant_sby.py --config VARIANT.json --formal DIR --repo DIR --rtl DIR --output DIR

Reads the variant's refinement config and writes one .sby per formal/*.sby to
--output with:
  * harness defines on every ``read -formal`` line (see the harness headers):
      reset sync_registered    RESET_SYNC_REGISTERED CLEAR_AT_START RESET_SYNC
      reset async              RESET_ASYNC ASYNC_RESET
      reset async_sync_release RESET_ASYNC_SYNC_RELEASE ASYNC_RESET RESET_SYNC
      debug_counters false     NO_COUNTERS
      pc_bits saturating_7     PC_SAT (and PCW=7)
      shift byte_lane          BYTE_LANE
  * chparam: DEPTH = fifo_words; PCW (engine_safety, timing_isolation) and IW
    (timing_isolation: log2(program_words)+1 with narrow_image_regs);
  * absolute [files] paths; ../src/protocol_emulator_core.v -> the variant core
    generated into --rtl.
The design-of-record .sby files are never modified. A summary is written to
--output/variant.txt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


def settings(config: dict) -> tuple[list[str], dict[str, int]]:
    options = config.get("options", {})
    architecture = config["architecture"]
    reset = options.get("reset", "sync")
    defines = {
        "sync": [],
        "sync_registered": ["RESET_SYNC_REGISTERED", "CLEAR_AT_START", "RESET_SYNC"],
        "async": ["RESET_ASYNC", "ASYNC_RESET"],
        "async_sync_release": ["RESET_ASYNC_SYNC_RELEASE", "ASYNC_RESET", "RESET_SYNC"],
    }[reset]
    if not options.get("debug_counters", True):
        defines.append("NO_COUNTERS")
    pcw = 24
    if options.get("pc_bits", "full") == "saturating_7":
        defines.append("PC_SAT")
        pcw = 7
    if options.get("shift", "barrel") == "byte_lane":
        defines.append("BYTE_LANE")
    iw = 16
    if options.get("narrow_image_regs", False):
        iw = (architecture["program_words"] - 1).bit_length() + 1
    return defines, {"DEPTH": architecture["fifo_words"], "PCW": pcw, "IW": iw}


def derive(text: str, defines: list[str], params: dict[str, int], formal: Path, repo: Path,
           rtl: Path) -> str:
    out = []
    section = ""
    flags = " ".join(f"-D{d}" for d in defines)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            out.append(line)
            continue
        if section == "[script]":
            prefix, body = "", line
            m = re.match(r"^(\s*~?[\w]+:\s*)(.*)$", line)
            if m and not line.lstrip().startswith(("read", "chparam", "prep")):
                prefix, body = m.group(1), m.group(2)
            if body.startswith("read -formal") and flags:
                body = body.replace("read -formal", f"read -formal {flags}", 1)
            if body.startswith("chparam") and "-set WIDTH" in body:
                module = body.split()[-1]
                body = re.sub(r"-set DEPTH \d+", f"-set DEPTH {params['DEPTH']}", body)
                extra = []
                if module in ("engine_safety", "timing_isolation"):
                    extra.append(f"-set PCW {params['PCW']}")
                if module == "timing_isolation":
                    extra.append(f"-set IW {params['IW']}")
                if extra:
                    body = body[: -len(module)] + " ".join(extra) + " " + module
            out.append(prefix + body)
            continue
        if section == "[files]" and stripped and not stripped.startswith("#"):
            m = re.match(r"^(\s*~?[\w]+:\s*)?(\S+)\s*$", line)
            prefix, path = (m.group(1) or ""), m.group(2)
            if path == "../src/protocol_emulator_core.v":
                resolved = rtl / "protocol_emulator_core.v"
            elif path.startswith("build/rtl/"):
                resolved = rtl / path[len("build/rtl/"):]
            else:
                resolved = (formal / path).resolve()
            if not resolved.exists() and not str(resolved).startswith(str(rtl)):
                raise SystemExit(f"variant_sby.py: missing {resolved}")
            out.append(f"{prefix}{resolved}")
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("config", "formal", "repo", "rtl", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    defines, params = settings(config)
    formal, output = Path(args.formal), Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    def write(path: Path, text: str) -> None:  # atomic: parallel jobs may derive concurrently
        tmp = path.with_name(f".{path.name}.{os.getpid()}")
        tmp.write_text(text)
        os.replace(tmp, path)

    for sby in sorted(formal.glob("*.sby")):
        write(output / sby.name, derive(sby.read_text(), defines, params, formal, Path(args.repo), Path(args.rtl)))
    summary = f"config {args.config}\ndefines {' '.join(defines) or '(none)'}\nparameters {params}\n"
    write(output / "variant.txt", summary)
    print("variant_sby.py: " + summary.replace("\n", "; "))


if __name__ == "__main__":
    main()
