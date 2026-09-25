(** ISA v2 encoding. Deliberately independent of the Hardcaml implementation. *)
type architecture = { engine_count : int; data_width : int; program_words : int;
                      fifo_words : int; issue : string; prefetch : bool }
type instruction = { mnemonic : string; a : int; b : int; c : int; imm : int }
val architecture_of_json : Yojson.Safe.t -> architecture
val architecture_to_json : architecture -> Yojson.Safe.t
val flagship : architecture
val opcode : string -> int

(** [byte_lane_shifts] (default false) additionally rejects SHL/SHR counts
    that are not a multiple of 8 (targets built with shift=byte_lane). *)
val encode : ?byte_lane_shifts:bool -> architecture -> owned_pins:int -> instruction -> int32

val minimum_cycles : instruction -> int
val blocking : instruction -> string
val instruction : ?a:int -> ?b:int -> ?c:int -> ?imm:int -> string -> instruction
val object_fields : string -> string list -> Yojson.Safe.t -> (string * Yojson.Safe.t) list
val required : string -> (string * Yojson.Safe.t) list -> Yojson.Safe.t
val json_int : string -> Yojson.Safe.t -> int
val json_string : string -> Yojson.Safe.t -> string
