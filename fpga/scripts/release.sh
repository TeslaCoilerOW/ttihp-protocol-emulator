#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Rebuild a published bitstream from fpga/scripts/release.tsv: the same
# build.sh options and the recorded placer seed.
#
#   fpga/scripts/release.sh NAME OUT_DIR     e.g. pe_cmod_a7_pll50 build/pe_cmod_a7_pll50
#   fpga/scripts/release.sh --list
#
# The rebuilt .bit differs from the published one only in the build time in
# its header; compare readback.json (configuration bits), not file hashes.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
table="$here/release.tsv"
if [ "${1:-}" = --list ]; then
  grep -v '^#' "$table" | cut -f1-4
  exit 0
fi
want="${1:?NAME (see --list)}"
out="${2:?OUT_DIR}"
line=$(awk -F'\t' -v n="$want" '!/^#/ && $1 == n' "$table")
[ -n "$line" ] || { echo "no release $want in $table" >&2; exit 2; }
IFS=$'\t' read -r name board clock run opts <<< "$line"
envs=()
[ "$opts" = - ] || read -r -a envs <<< "$opts"
echo "release $name: $board $clock $run ${envs[*]:-}"
env "${envs[@]}" bash "$here/build.sh" "$board" "$clock" "$out" "$run"
