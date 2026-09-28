#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# First-hour bring-up of a board: program the capture-unit bitstream, then
# run fpga/host/bringup.py (link, selftest, jumper prompt, loopback,
# capture-unit checks, self-measured timing-isolation experiment).
# docs/fpga.md, "First hour with a board", lists the wiring, the expected
# output and what to do when a step fails.
#
#   fpga/scripts/bringup.sh cmod_a7 [PORT] [bringup.py options]
#   fpga/scripts/bringup.sh urbana  [PORT] [bringup.py options]
#
#   PORT   the board's UART, the second of its two USB serial ports
#          (default /dev/ttyUSB1)
#
# Environment:
#   PE_BITSTREAMS   directory holding pe_cmod_a7_pll50.bit / pe_urbana_pll50.bit
#                   of the capture-unit release (the Vivado set
#                   vivado-2025.2-2026-09-27 first, or the openXC7 set
#                   v3-2026-09-27-scope), with its SHA256SUMS (required
#                   unless PE_BITSTREAM is set)
#   PE_BITSTREAM    program this .bit instead
#   PE_SKIP_PROGRAM=1  the board is already programmed
#   PE_BRINGUP_OUT  output directory (default bringup-<board>-<date>)
#   OPENFPGALOADER  openFPGALoader command (default: openFPGALoader; the
#                   recorded tool is v1.1.1 from OSS CAD Suite 20260729)
#   PYTHON          Python 3.8+ with pyserial (fpga/host/requirements.txt)
set -euo pipefail
board="${1:?BOARD (cmod_a7|urbana)}"
port="${2:-/dev/ttyUSB1}"
shift $(( $# >= 2 ? 2 : 1 ))
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
case "$board" in
  cmod_a7) ofl_board=cmoda7_35t; bitname=pe_cmod_a7_pll50.bit ;;
  urbana)  ofl_board=arty_s7_50; bitname=pe_urbana_pll50.bit ;;
  *) echo "unknown board $board (cmod_a7 or urbana)" >&2; exit 2 ;;
esac
ofl="${OPENFPGALOADER:-openFPGALoader}"
py="${PYTHON:-python3}"
out="${PE_BRINGUP_OUT:-bringup-$board-$(date +%Y%m%d-%H%M%S)}"

echo "== tools"
"$py" -c 'import sys; assert sys.version_info >= (3, 8), sys.version; print("python", sys.version.split()[0])'
"$py" -c 'import serial; print("pyserial", serial.__version__)' ||
  { echo "pyserial missing: $py -m pip install -r $repo/fpga/host/requirements.txt" >&2; exit 2; }
tty="$(basename "$port")"
if [ -r "/sys/bus/usb-serial/devices/$tty/latency_timer" ]; then
  lat="$(cat "/sys/bus/usb-serial/devices/$tty/latency_timer")"
  echo "$port: FTDI latency timer $lat ms (1 ms gives denser host traffic in the loaded phase; see docs/fpga.md)"
fi

if [ "${PE_SKIP_PROGRAM:-0}" != 1 ]; then
  bit="${PE_BITSTREAM:-${PE_BITSTREAMS:?set PE_BITSTREAMS to the bitstream directory (or PE_BITSTREAM)}/$bitname}"
  [ -f "$bit" ] || { echo "no bitstream $bit" >&2; exit 2; }
  sums="$(dirname "$bit")/SHA256SUMS"
  if [ -f "$sums" ] && grep -q " $(basename "$bit")\$" "$sums"; then
    (cd "$(dirname "$bit")" && grep " $(basename "$bit")\$" SHA256SUMS | sha256sum -c -)
  else
    echo "note: no SHA256SUMS entry for $(basename "$bit"); not verified"
  fi
  echo "== program ($ofl $("$ofl" --Version 2>&1 | head -n 1))"
  "$ofl" -b "$ofl_board" --detect
  "$ofl" -b "$ofl_board" "$bit"
  sleep 1
fi

echo "== bring-up ($out)"
exec "$py" "$repo/fpga/host/bringup.py" --port "$port" --board "$board" --out "$out" "$@"
