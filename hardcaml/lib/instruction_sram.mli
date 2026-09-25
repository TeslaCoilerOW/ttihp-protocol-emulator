type inputs = {
  clock : Hardcaml.Signal.t; clear : Hardcaml.Signal.t; write : Hardcaml.Signal.t;
  write_address : Hardcaml.Signal.t; write_data : Hardcaml.Signal.t;
  next_pc : Hardcaml.Signal.t;
}
type t = {
  instruction : Hardcaml.Signal.t; address : Hardcaml.Signal.t;
  memory_enable : Hardcaml.Signal.t; write_enable : Hardcaml.Signal.t;
  read_enable : Hardcaml.Signal.t;
}

(** Fixed pair of 64x16 single-port macros.  Instruction width remains 32. *)
val create : engine_index:int -> inputs -> t

(** Two-state synchronous model for tests; never selected by the RTL generator. *)
val create_model : engine_index:int -> inputs -> t
