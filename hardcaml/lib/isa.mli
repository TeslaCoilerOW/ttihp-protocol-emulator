(** ISA v2 encoding: the assembler's view of the instruction set (docs/isa.md).
    The operand rules here are written from the ISA document, separately from
    the hardware decoder ({!Engine_decode}); only the opcode list
    ({!Opcode}) is shared. *)

type architecture = { engine_count : int; data_width : int; program_words : int;
                      fifo_words : int; issue : Issue.t; prefetch : bool }

(** One instruction with its operand fields. Formats with an immediate use
    [imm] (24 bits, or 16 bits beside register [a] for LOAD and JZ); the
    others use [a], [b] and [c]. Unused fields must be zero. *)
type instruction = { op : Opcode.t; a : int; b : int; c : int; imm : int }

val architecture_of_json : Yojson.Safe.t -> architecture
val architecture_to_json : architecture -> Yojson.Safe.t

(** The design of record's architecture. *)
val flagship : architecture

(** [byte_lane_shifts] (default false) additionally rejects SHL/SHR counts
    that are not a multiple of 8 (targets built with shift=byte_lane).
    [line_unit] (default false) accepts the line-unit instructions LTIM, LCFG,
    CRC, LSTAT and the XFER line/CRC flags (docs/extension.md); without it
    they are rejected. Raises [Invalid_argument] with the reason when the
    instruction is not valid for the target. *)
val encode : ?byte_lane_shifts:bool -> ?line_unit:bool -> architecture -> owned_pins:int ->
  instruction -> int32

(** Cycles from issue to the next issue when nothing blocks: [1 + imm] for
    WAIT, [1 + 2ab] for XFER, otherwise 1. *)
val minimum_cycles : instruction -> int

(** How the instruction can block: ["tx_fifo_unbounded"] (PULL),
    ["rx_fifo_unbounded"] (blocking PUSH), ["pin_limit"] (WAITPIN),
    ["event_limit"] (WAITEVENT) or ["none"]. *)
val blocking : instruction -> string

val instruction : ?a:int -> ?b:int -> ?c:int -> ?imm:int -> Opcode.t -> instruction

(** JSON helpers shared with {!Assembler}; each raises [Invalid_argument]. *)
val object_fields : string -> string list -> Yojson.Safe.t -> (string * Yojson.Safe.t) list
val required : string -> (string * Yojson.Safe.t) list -> Yojson.Safe.t
val json_int : string -> Yojson.Safe.t -> int
val json_string : string -> Yojson.Safe.t -> string
