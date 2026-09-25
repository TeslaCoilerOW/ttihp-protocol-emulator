(** Fixed optional payload macros, separate from private instruction storage. *)
type capacity = KiB2 | KiB4
val words : capacity -> int
val address_bits : capacity -> int
val macro_name : capacity -> string

type inputs = {
  clock : Hardcaml.Signal.t;
  clear : Hardcaml.Signal.t;
  request : Hardcaml.Signal.t;
  write : Hardcaml.Signal.t;
  address : Hardcaml.Signal.t;
  write_data : Hardcaml.Signal.t;
  tag : Hardcaml.Signal.t;
}

type t = {
  ready : Hardcaml.Signal.t;
  accepted : Hardcaml.Signal.t;
  read_valid : Hardcaml.Signal.t;
  read_data : Hardcaml.Signal.t;
  read_tag : Hardcaml.Signal.t;
  memory_enable : Hardcaml.Signal.t;
  write_enable : Hardcaml.Signal.t;
  read_enable : Hardcaml.Signal.t;
}

(** One full-word operation per rising system-clock edge. A read accepted at
    edge N supplies data and its opaque four-bit tag after edge N, to be captured
    by the recipient at N+1. The recipient must reserve capacity before issue;
    there is no response backpressure or internal response queue. Clear blocks
    access immediately; it must span a rising edge to synchronously invalidate
    response metadata, without clearing memory or DOUT. A pulse entirely between
    edges only masks a response temporarily; it is not an asynchronous reset.
    Invalid response data/tag are masked to zero. Reads before initialization
    remain undefined when read_valid is high. The caller owns bounds, descriptor
    ownership, credits and timestamp provenance. *)
val create : capacity -> inputs -> t

(** Two-state Cyclesim model only. Its zero-initialized simulation storage does
    not establish silicon initialization or four-state vendor-model behavior. *)
val create_model : capacity -> inputs -> t
