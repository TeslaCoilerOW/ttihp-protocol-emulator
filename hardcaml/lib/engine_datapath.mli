(** The architectural state of one engine and the register-transfer helpers
    that the execute, transfer and line-unit stages share.

    Every register here takes the engine's clear (synchronous clear, or
    asynchronous reset in the asynchronous variants) and is written only from
    the [Always] tree that {!Engine.create} compiles; with
    {!Timing_options.t.split_instruction_decode}, [completed] is written from
    a second [compile] of its own in {!Engine.create} (see {!start}). *)

open Hardcaml

type t = {
  pc : Always.Variable.t;  (** [pc]: 24 bits, or 7 with [pc_bits=saturating_7] *)
  running : Always.Variable.t;  (** [running] *)
  fault : Always.Variable.t;  (** [fault_code]: 0 = no fault *)
  registers : Always.Variable.t array;
      (** [tx], [rx], [x], [y]: the register file, indexed by the 2-bit
          register number of an instruction *)
  repeat_count : Always.Variable.t;  (** [repeat_count]: COUNT/LOOP *)
  timer : Always.Variable.t;  (** [wait_timer]: WAIT cycles still to go *)
  limit : Always.Variable.t;  (** [wait_limit]: LIMIT for WAITPIN/WAITEVENT *)
  blocked : Always.Variable.t;  (** [blocked_cycles]: of the current WAITPIN/WAITEVENT *)
  values : Always.Variable.t;  (** [logical_output]: pin values before ownership masking *)
  enables : Always.Variable.t;  (** [logical_enable]: pin output enables *)
  transfer_pins : Always.Variable.t;
      (** [transfer_pins] (PINS): clock pin in 2:0, data-out pin in 5:3, data-in pin in 8:6 *)
  completed : Always.Variable.t;  (** [completed_instructions] (READ_SELECT 5) *)
  transfer_edges : Always.Variable.t;
      (** [transfer_edges]: XFER edges still to go (data bits in a line XFER) *)
  transfer_tick : Always.Variable.t;  (** [transfer_tick]: cycles to the next XFER edge *)
  transfer_period : Always.Variable.t;  (** [transfer_period]: the XFER half period *)
  transfer_mode : Always.Variable.t;  (** [transfer_mode]: XFER flags (c) of the transfer in progress *)
}

(** The registers above, with the given widths. *)
val create : Reg_spec.t -> pc_width:int -> data_width:int -> transfer_mode_width:int -> t

(** Register 0 (TX shifter) and register 1 (RX shifter). *)
val tx : t -> Always.Variable.t
val rx : t -> Always.Variable.t

(** [write_register t ~dest data]: register [dest] (2 bits) := [data]. *)
val write_register : t -> dest:Signal.t -> Signal.t -> Always.t list

(** One-hot 8-bit mask of a 3-bit pin number. *)
val bitmask : Signal.t -> Signal.t

(** [write_pin values pin bit]: [values] with bit [pin] replaced by [bit]. *)
val write_pin : Signal.t -> Signal.t -> Signal.t -> Signal.t

(** The TX register shifted by one: left when [msb] is 1, else right. *)
val shift_tx : t -> msb:Signal.t -> Signal.t

(** The bit of [data] that goes out first: its MSB when [msb] is 1, else its LSB. *)
val tx_bit : msb:Signal.t -> Signal.t -> Signal.t

(** The RX register with [data] shifted in: at the LSB when [msb] is 1
    (left shift), else at the MSB (right shift). *)
val sample : t -> msb:Signal.t -> Signal.t -> Signal.t

(** The register assignments of START: PC 0, running, no fault, LIMIT 65535,
    and every other register above 0. [clear_completed] false leaves
    [completed] out (it then has its own always block, see
    {!Timing_options.t.split_instruction_decode}). *)
val start : t -> clear_completed:bool -> Always.t list
