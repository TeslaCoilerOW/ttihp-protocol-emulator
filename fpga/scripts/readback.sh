#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Bit-level readback of a build: regenerate the frames from nextpnr's FASM
# (fasm2frames), decode the .bit with bitread, and require the same set of
# configuration bits (bit_readback.py). Writes readback.json in BUILD_DIR.
#
#   fpga/scripts/readback.sh BUILD_DIR [PART]   (a build.sh output directory;
#                                              PART defaults to summary.json's)
set -euo pipefail
dir="${1:?BUILD_DIR}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${OPENXC7:?set OPENXC7 to the unpacked tools-openxc7 package}"
bit=$(ls "$dir"/pe_*.bit | head -n 1)
name=$(basename "$bit" .bit)
part="${2:-$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['part'])" "$dir/summary.json")}"
case "$part" in xc7a*) family=artix7 ;; xc7s*) family=spartan7 ;; *) echo "unknown part $part" >&2; exit 2 ;; esac
db="$OPENXC7/share/nextpnr/external/prjxray-db/$family"
frames="$dir/readback.frames"
"$OPENXC7/bin/fasm2frames" --part "$part" --db-root "$db" "$dir/$name.fasm" 2>/dev/null | grep '^0x' > "$frames"
if python3 "$here/bit_readback.py" "$OPENXC7/bin/bitread" "$db/$part/part.yaml" "$frames" "$bit" > "$dir/readback.json"; then
  status=PASS
else
  status=FAIL
fi
rm -f "$frames"
echo "readback $status: $(tr -d '\n' < "$dir/readback.json" | tr -s ' ')"
[ "$status" = PASS ]
