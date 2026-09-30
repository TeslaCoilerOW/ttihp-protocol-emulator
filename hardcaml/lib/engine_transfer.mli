(** XFER, the committed timed transfer (docs/isa.md opcode 17; line mode in
    docs/extension.md).

    Issuing an XFER loads the transfer registers of {!Engine_datapath}
    ([transfer_edges], [transfer_tick], [transfer_period], [transfer_mode])
    and drives the first clock level; from the next cycle the engine runs
    {!transfer} instead of issuing instructions until [transfer_edges] is 0.
    A transfer performs no queue access. *)

open Hardcaml

(** Width of [transfer_mode]: the five flag bits CPOL, CPHA, MSB first, drive
    and sample, plus bit 5 (line mode) and bit 6 (feed the CRC) with the line
    unit. *)
val mode_width : line_unit:bool -> int

(** Execute body of XFER. A classic XFER sets 2a edges at half period b and
    drives the clock pin to its idle level (CPOL), and for CPHA 0 with drive
    also the first data bit. With the line unit, a line XFER (c bit 5) only
    arms the unit for a data bits (the ticker keeps running), and a classic
    XFER takes over the shared tick and period registers and stops the
    ticker. *)
val issue :
  Engine_datapath.t ->
  fields:Engine_decode.fields ->
  values_now:Signal.t ->
  line:Engine_line.t option ->
  Always.t list

(** One cycle of a transfer in progress. A classic transfer counts down the
    half period; at each edge it toggles the clock pin, shifts TX out on the
    shifting edge and samples the data-in pin into RX on the sampling edge
    (per CPOL/CPHA), and completes with the last edge. With the line unit a
    classic transfer can also feed each bit to the CRC (c bit 6), and a line
    XFER runs {!Engine_line.t.transfer}. [pins] are the synchronized pin
    inputs; [finish] is the engine's "instruction completes" list. *)
val transfer :
  Engine_datapath.t ->
  pins:Signal.t ->
  values_now:Signal.t ->
  finish:Always.t list ->
  line:Engine_line.t option ->
  Always.t list
