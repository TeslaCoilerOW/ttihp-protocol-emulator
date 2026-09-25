#!/bin/bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Run the *upstream* test_random (snapshot test/Makefile, unmodified) for one seed,
# against the prebuilt sim.vvp, with a private results file; used for the fidelity
# check (campaign driver vs upstream test on the same seed) and for triage replays.
#   upstream_run.sh <snapshot> <sim_build> <log> [VAR=value ...]
# (gate level: export VCAMP_NETLIST=<netlist> and pass the gl build)
# e.g. upstream_run.sh $SNAP $BUILD/rtl out.log PE_SEED=1
#      upstream_run.sh $SNAP $BUILD/rtl out.log PE_REPLAY=/path/case.json
set -u
SNAP=$1 BUILD=$2 LOG=$3
shift 3
source ${PE_WORK:?set PE_WORK to the cluster work directory}/cocotb/env20.sh
work=$(mktemp -d "${TMPDIR:-/tmp}/pe-vcamp-up.XXXXXX")
# Private copy of the build: if make decides the sources are newer (e.g. a copied
# snapshot), it rebuilds here and never rewrites the shared sim.vvp that campaign
# tasks are loading.
cp -p "$BUILD/sim.vvp" "$BUILD/cmds.f" "$work/"
BUILD=$work
cd "$SNAP/test" || exit 99
GATES_ARGS=()
if [ -n "${VCAMP_NETLIST:-}" ]; then
  GATES_ARGS=(GATES=yes GL_NETLIST="$VCAMP_NETLIST")
fi
env PYTHONDONTWRITEBYTECODE=1 "$@" make SIM_BUILD="$BUILD" WAVES=none "${GATES_ARGS[@]}" \
  COCOTB_TEST_MODULES=test_random COCOTB_RESULTS_FILE="$work/results.xml" >"$LOG" 2>&1
rc=$?
grep -E "FAIL=|PASS=" "$LOG" | tail -3
rm -rf "$work"
exit $rc
