#!/bin/bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Compile the RTL (or gate-level) simulation once with the snapshot's own
# test/Makefile, into a shared SIM_BUILD that every campaign task then runs with vvp.
#   build.sh rtl <snapshot> <sim_build_dir>
#   build.sh gl  <snapshot> <sim_build_dir> <netlist.v>
# Run on a compute node (Slurm), never on a login node.
set -euo pipefail
MODE=$1 SNAP=$2 BUILD=$3 NETLIST=${4:-}
source ${PE_WORK:?set PE_WORK to the cluster work directory}/cocotb/env20.sh
cd "$SNAP/test"
mkdir -p "$BUILD"
if [ "$MODE" = rtl ]; then
  make SIM_BUILD="$BUILD" WAVES=none "$BUILD/sim.vvp"
else
  make GATES=yes GL_NETLIST="$NETLIST" SIM_BUILD="$BUILD" WAVES=none "$BUILD/sim.vvp"
fi
ls -la "$BUILD"
sha256sum "$BUILD/sim.vvp" | tee "$BUILD/sim.vvp.sha256"
