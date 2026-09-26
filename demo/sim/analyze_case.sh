#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# Capture analysis of one simulation case (demo/sim/run_case.sh), the same
# analysis a hardware capture gets. Re-runnable on a finished case directory:
#
#   demo/sim/analyze_case.sh DEMO_WORK/runs/TAG
#
# Reads case.json, result.json and sim.vcd.gz; writes predicted.json,
# exact.{json,txt} (the simulation VCD itself, cycles counted from the
# clock) and la.{json,txt} (an emulated logic-analyser capture):
#   pins   : 24 MHz, 8 channels (fx2lafw-class): D0 host clock, D1/D2 probe
#            pins 6/7, D3 uio0 (UART), D4 uio2 (SCK), D5 ui_in[4] (write
#            valid), D6 ui_in[5] (read ready), D7 uio1; cycles from the clock channel
#   bridge : 120 MHz, 4 channels (Pico analyser): D0/D1 probe pins, D2 uio0,
#            D3 uio2; 400,000-sample captures; seeded crystal offset and
#            phase; cycles recovered from the 12 MHz grid (--fclk)
# test_four: a 2 MHz capture of all eight pads (four.sr), decoded with
# sigrok's UART/SPI/I2C decoders when SIGROK_CLI is set.

set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
out=$(cd "$1" && pwd)
export PYTHONDONTWRITEBYTECODE=1
cap="python3 $REPO/demo/pe_capture.py"
vcd=$out/sim.vcd.gz
read -r tag tb mode core seed test < <(python3 -c "
import json; c = json.load(open('$out/case.json'))
print(c['tag'], c['tb'], c['mode'], c['core'], c['seed'], c['test'])")
[ -f "$vcd" ] || exit 0

if [ "$test" = test_isolation ]; then
  expect=55,a3,0f,c6,39,e1
  # The measured phase (probe START to the end of the idle wait or host traffic).
  window=$(python3 -c "import json; a, b = json.load(open('$out/result.json'))['phase_ns']; print(f'{a}:{b}')")
  # Frames are checked against the probe's schedule from the static timing analyser.
  $cap predict --image "$REPO/demo/firmware/timing-probe.image.json" --json "$out/predicted.json" > /dev/null
  ref="--reference $out/predicted.json"
  rm -f "$out"/la24.sr "$out"/pla-*.sr
  if [ "$tb" = pins ]; then
    $cap analyze "$vcd" --label "$tag exact" --clock tb.clk \
      --channels 'probe6=tb.pads[6],probe7=tb.pads[7]' \
      --context 'uart=tb.pads[0],sck=tb.pads[2],wvalid=tb.ui_in[4],rready=tb.ui_in[5]' \
      --uart uart:64 --uart-expect "$expect" --window-ns "$window" $ref --json "$out/exact.json" > "$out/exact.txt"
    $cap resample "$vcd" --rate 24m --ppm 0 --phase 0.5 --window-ns "$window" -o "$out/la24.sr" \
      --channels 'D0=tb.clk,D1=tb.pads[6],D2=tb.pads[7],D3=tb.pads[0],D4=tb.pads[2],D5=tb.ui_in[4],D6=tb.ui_in[5],D7=tb.pads[1]' \
      > "$out/resample.txt"
    $cap analyze "$out/la24.sr" --label "$tag 24MHz" --clock D0 --channels probe6=D1,probe7=D2 \
      --context uart=D3,sck=D4,wvalid=D5,rready=D6 --uart uart:64 --uart-expect "$expect" \
      $ref --json "$out/la.json" > "$out/la.txt"
  else
    ppm=$(python3 -c "import random; r=random.Random($seed*7919+len('$core')); print(round(r.uniform(-80,80),1))")
    phase=$(python3 -c "import random; r=random.Random($seed*104729+len('$mode')); print(round(r.random(),3))")
    $cap analyze "$vcd" --label "$tag exact" --clock tb_bridge.clk \
      --channels 'probe6=tb_bridge.pads[6],probe7=tb_bridge.pads[7]' \
      --context 'uart=tb_bridge.pads[0],sck=tb_bridge.pads[2],wvalid=tb_bridge.dut.shell.tt.ui_in[4],rready=tb_bridge.dut.shell.tt.ui_in[5]' \
      --uart uart:64 --uart-expect "$expect" --window-ns "$window" $ref --json "$out/exact.json" > "$out/exact.txt"
    $cap resample "$vcd" --rate 120m --ppm "$ppm" --phase "$phase" --segment-samples 400000 --window-ns "$window" \
      -o "$out/pla.sr" --channels 'D0=tb_bridge.pads[6],D1=tb_bridge.pads[7],D2=tb_bridge.pads[0],D3=tb_bridge.pads[2]' \
      > "$out/resample.txt"
    $cap analyze "$out"/pla-*.sr --label "$tag 120MHz" --fclk 12e6 --channels probe6=D0,probe7=D1 \
      --context uart=D2,sck=D3 --uart uart:64 --uart-expect "$expect" $ref --json "$out/la.json" > "$out/la.txt"
  fi
fi

if [ "$test" = test_four ]; then
  # Fixed 1 us host clock: UART 434 cycles/bit = 2304 baud, I2C and SPI scaled likewise.
  $cap resample "$vcd" --rate 2m -o "$out/four.sr" \
    --channels 'D0=tb.pads[0],D1=tb.pads[1],D2=tb.pads[2],D3=tb.pads[3],D4=tb.pads[4],D5=tb.pads[5],D6=tb.pads[6],D7=tb.pads[7]' \
    > "$out/resample.txt"
  if [ -n "${SIGROK_CLI:-}" ]; then
    $SIGROK_CLI -i "$out/four.sr" -P uart:rx=D0:baudrate=2304 -A uart=rx-data > "$out/sigrok-uart-tx.txt" 2>&1 || true
    $SIGROK_CLI -i "$out/four.sr" -P uart:rx=D1:baudrate=2304 -A uart=rx-data > "$out/sigrok-uart-rx.txt" 2>&1 || true
    $SIGROK_CLI -i "$out/four.sr" -P spi:clk=D2:mosi=D3:miso=D4:cs=D5:cpol=0:cpha=0:wordsize=8 \
      -A spi=mosi-data:miso-data > "$out/sigrok-spi.txt" 2>&1 || true
    $SIGROK_CLI -i "$out/four.sr" -P i2c:scl=D6:sda=D7 \
      -A i2c=address-read:address-write:data-read:data-write:ack:nack:start:stop > "$out/sigrok-i2c.txt" 2>&1 || true
  fi
fi
