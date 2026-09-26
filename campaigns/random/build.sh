#!/bin/bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Compile the RTL (or gate-level) simulation once with the snapshot's own
# test/Makefile, into a shared SIM_BUILD that every campaign task then runs with vvp.
#   build.sh rtl <snapshot> <sim_build_dir>
#   build.sh gl  <snapshot> <sim_build_dir> <netlist.v>
# Run on a compute node (Slurm), never on a login node.
# A design variant (test/README.md, "Design variants") is selected the way the
# snapshot's Makefile selects it: PE_VARIANT=<name> compiles
# <snapshot>/build/variants/<name>/protocol_emulator_core.v (scripts/gen_variants.sh)
# or PE_CORE=<path>; unset means base, exactly as before. The campaign tasks must
# then run with the same PE_VARIANT (config DESIGN_VARIANT, see submit.sh).
set -euo pipefail
MODE=$1 SNAP=$2 BUILD=$3 NETLIST=${4:-}
source ${PE_WORK:?set PE_WORK to the cluster work directory}/cocotb/env20.sh
cd "$SNAP/test"
mkdir -p "$BUILD"
if [ -n "${PE_VARIANT:-}" ]; then
  export PE_VARIANT
  core=${PE_CORE:-$SNAP/build/variants/$PE_VARIANT/protocol_emulator_core.v}
  echo "design variant $PE_VARIANT, core $core"
  if [ "$MODE" = rtl ] && [ "$PE_VARIANT" != base ]; then
    [ -f "$core" ] || { echo "build.sh: missing $core (run scripts/gen_variants.sh $PE_VARIANT)" >&2; exit 2; }
    sha256sum "$core" | tee "$BUILD/core.sha256"
  fi
fi
if [ "$MODE" = rtl ]; then
  make SIM_BUILD="$BUILD" WAVES=none "$BUILD/sim.vvp"
else
  make GATES=yes GL_NETLIST="$NETLIST" SIM_BUILD="$BUILD" WAVES=none "$BUILD/sim.vvp"
fi
ls -la "$BUILD"
sha256sum "$BUILD/sim.vvp" | tee "$BUILD/sim.vvp.sha256"
