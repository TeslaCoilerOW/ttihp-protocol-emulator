#!/usr/bin/env bash
# Copyright (c) 2026 TeslaCoilerOW
# SPDX-License-Identifier: Apache-2.0
#
# sigrok-cli captures for the hardware demonstration (docs/demo.md). Prints
# the command, then runs it (DRY=1 only prints). Wiring: docs/demo.md.
#
#   demo/capture.sh isolation-clock OUT.sr [SECONDS]
#       fx2lafw-class analyser (8 channels), host-clock build + Pico host.
#       8 MHz, channels: D0 clk, D1 probe6, D2 probe7, D3 uart (uio0),
#       D4 sck (uio2), D5 wvalid (ui_in[4]), D6 rready (ui_in[5]), D7 marker.
#   demo/capture.sh isolation-realtime OUT-PREFIX [COUNT]
#       sigrok-pico analyser (Raspberry Pi Pico), osc12 bridge build.
#       COUNT captures of 400,000 samples at 120 MHz (3.3 ms each; the
#       firmware's fixed-depth limit for up to 4 channels), channels
#       D2 probe6, D3 probe7, D4 uart (uio0), D5 sck (uio2).
#   demo/capture.sh four OUT.sr [SECONDS]
#       fx2lafw-class analyser, 24 MHz, all eight protocol pins:
#       D0 tx, D1 rx, D2 sck, D3 mosi, D4 miso, D5 cs, D6 scl, D7 sda,
#       then sigrok's UART (115200), SPI and I2C decoders.
#
# Environment: SIGROK_CLI (default sigrok-cli), FX2_DRIVER (default fx2lafw),
# PICO_LA (default raspberrypi-pico:conn=/dev/ttyACM0:serialcomm=115200/flow=0).

set -euo pipefail
cli=${SIGROK_CLI:-sigrok-cli}
fx2=${FX2_DRIVER:-fx2lafw}
pico=${PICO_LA:-raspberrypi-pico:conn=/dev/ttyACM0:serialcomm=115200/flow=0}

run() {
  echo "+ $*"
  if [ "${DRY:-0}" != 1 ]; then
    "$@"
  fi
}

preset=${1:?preset: isolation-clock | isolation-realtime | four}
out=${2:?output file or prefix}
case "$preset" in
  isolation-clock)
    secs=${3:-30}
    # shellcheck disable=SC2086
    run $cli -d "$fx2" --config samplerate=8m --time "${secs}s" \
      --channels D0=clk,D1=probe6,D2=probe7,D3=uart,D4=sck,D5=wvalid,D6=rready,D7=marker -o "$out"
    ;;
  isolation-realtime)
    count=${3:-10}
    for i in $(seq -w 1 "$count"); do
      # shellcheck disable=SC2086
      run $cli -d "$pico" --config samplerate=120m --samples 400000 \
        --channels D2=probe6,D3=probe7,D4=uart,D5=sck -o "$out-$i.sr"
    done
    ;;
  four)
    secs=${3:-2}
    # shellcheck disable=SC2086
    run $cli -d "$fx2" --config samplerate=24m --time "${secs}s" \
      --channels D0=tx,D1=rx,D2=sck,D3=mosi,D4=miso,D5=cs,D6=scl,D7=sda -o "$out"
    # shellcheck disable=SC2086
    run $cli -i "$out" -P uart:rx=tx:baudrate=115200 -A uart=rx-data
    # shellcheck disable=SC2086
    run $cli -i "$out" -P uart:rx=rx:baudrate=115200 -A uart=rx-data
    # shellcheck disable=SC2086
    run $cli -i "$out" -P spi:clk=sck:mosi=mosi:miso=miso:cs=cs:cpol=0:cpha=0:wordsize=8 -A spi=mosi-data:miso-data
    # shellcheck disable=SC2086
    run $cli -i "$out" -P i2c:scl=scl:sda=sda
    ;;
  *)
    echo "unknown preset $preset" >&2
    exit 2
    ;;
esac
