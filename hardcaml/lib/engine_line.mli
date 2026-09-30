(** The line unit inside an engine (Line_options.Rec16, docs/extension.md): a
    free-running bit ticker that shares the XFER tick and period registers,
    NRZ/NRZI/Manchester line coding, bit stuffing, a complementary pin pair
    with SE0 detection, an arbitration monitor and a CRC; the opcodes LTIM,
    LCFG, CRC and LSTAT and the line mode of XFER.

    {!Engine.create} builds it only when the knob is set. Without it no
    signal of this module exists, and the engine is the design of record's.
    The combinational CRC step and the seeded defects are in {!Line_unit}. *)

open Hardcaml

(** The unit's registers, as a Hardcaml interface: [port_names] are the RTL
    register names ([Engine.line_register_names]). Written out by hand (the
    library has no ppx_hardcaml); [\[@@deriving hardcaml\]] would generate the
    same functions. *)
module Registers : sig
  type 'a t = {
    run : 'a;  (** [line_run]: ticker running *)
    phase : 'a;  (** [line_phase]: 0, the next tick is a bit boundary; 1, mid-bit *)
    frac : 'a;  (** [line_frac]: fraction Q/256 added per tick *)
    acc : 'a;  (** [line_acc]: phase accumulator *)
    boundary_seen : 'a;  (** [line_boundary_seen]: a drive+sample XFER has driven its first boundary *)
    cfg : 'a;  (** [line_cfg]: LCFG bits 9:0 *)
    level : 'a;  (** [line_level]: line level last driven *)
    rx_prev : 'a;  (** [line_rx_prev]: previous raw sample (NRZI decoding) *)
    cell_bit : 'a;  (** [line_cell_bit]: bit of the current cell (Manchester, arbitration) *)
    man_pending : 'a;  (** [line_man_pending]: Manchester second half pending *)
    se0 : 'a;  (** [line_se0]: a sampling XFER ended on SE0 *)
    remaining : 'a;  (** [line_remaining]: data bits remaining when it did *)
    trailing_stuff : 'a;  (** [line_trailing_stuff]: the XFER continues for a trailing stuff bit *)
    stuff_run : 'a;  (** [stuff_run]: current run length *)
    stuff_last : 'a;  (** [stuff_last]: last line bit *)
    stuff_error : 'a;  (** [stuff_error]: a received stuff bit had the wrong value *)
    arbitration_lost : 'a;  (** [arbitration_lost] *)
    crc_state : 'a;  (** [crc_state]: CRC register ({!Line_unit.crc_width} bits) *)
    crc_preset : 'a;  (** [crc_preset]: CRC polynomial preset *)
  }

  include Interface.S with type 'a t := 'a t

  (** Named register variables: [Of_always.reg] with [Of_always.apply_names]. *)
  val create : Reg_spec.t -> Always.Variable.t t
end

type t = {
  registers : Always.Variable.t Registers.t;
  values_now : Signal.t;
      (** the logical pin values after this cycle's Manchester second half;
          instruction pin writes build on it *)
  every_cycle : Always.t list;
      (** the ticker and the Manchester second half, in every active cycle *)
  ltim : Always.t list;  (** execute bodies of LTIM, LCFG, CRC and LSTAT *)
  lcfg : Always.t list;
  crc : Always.t list;
  lstat : Always.t list;
  on_start : Always.t list;  (** START: every register above := 0 *)
  transfer : Always.t list;
      (** a line-mode XFER in progress (bit 5 of [transfer_mode]):
          [transfer_edges] counts data bits *)
  crc_step : msb:Signal.t -> Signal.t -> Always.t list;
      (** feed one bit to the CRC in the given bit order *)
}

(** [finish] is the engine's shared "instruction completes" assignment list;
    [source] the register selected by b; [pins] the synchronized pin inputs. *)
val create :
  ?mutation:Line_unit.mutation ->
  Always.Variable.t Registers.t ->
  Engine_datapath.t ->
  fields:Engine_decode.fields ->
  source:Signal.t ->
  finish:Always.t list ->
  pins:Signal.t ->
  tx_valid:Signal.t ->
  rx_ready:Signal.t ->
  t
