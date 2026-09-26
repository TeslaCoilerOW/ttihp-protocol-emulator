type t = {
  ready : Hardcaml.Signal.t;
  valid : Hardcaml.Signal.t;
  data : Hardcaml.Signal.t;
  level : Hardcaml.Signal.t;
}

(** Storage implementation.  [Memory] ([create]) is the design of record: a
    memory with no reset.  The register forms make every storage word a
    write-enabled register cleared by the given chip reset net, either
    synchronously or asynchronously (never by FLUSH). *)
type storage = Memory | Registers_sync_clear of Hardcaml.Signal.t
             | Registers_async_reset of Hardcaml.Signal.t

(** Design of record: [create_with ~async_reset:None ~storage:Memory]. *)
val create : clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  width:int -> depth:int -> push:Hardcaml.Signal.t -> pop:Hardcaml.Signal.t ->
  data:Hardcaml.Signal.t -> t

(** [clear] is a synchronous clear of the occupancy (chip clear OR FLUSH in the
    design of record; FLUSH only when [async_reset] carries the chip reset,
    which then also resets count and pointers asynchronously). *)
val create_with : async_reset:Hardcaml.Signal.t option -> storage:storage ->
  clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  width:int -> depth:int -> push:Hardcaml.Signal.t -> pop:Hardcaml.Signal.t ->
  data:Hardcaml.Signal.t -> t

(** [create_with] whose storage write happens one edge after the push (timing
    knob [fifo_write_staging], docs/timing-closure.md): the accepted word waits
    in a staging register, and reads of its slot bypass to it, so [ready],
    [valid], [level] and [data] are those of [create_with] at every cycle. *)
val create_staged : async_reset:Hardcaml.Signal.t option -> storage:storage ->
  clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  width:int -> depth:int -> push:Hardcaml.Signal.t -> pop:Hardcaml.Signal.t ->
  data:Hardcaml.Signal.t -> t

(** [create_staged] whose acceptance ([put], [take]) is gated by [gate]
    instead of [clear]; [clear] still clears the registers (timing knob
    [clear_outputs_only]). *)
val create_staged_gated : gate:Hardcaml.Signal.t -> async_reset:Hardcaml.Signal.t option ->
  storage:storage -> clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  width:int -> depth:int -> push:Hardcaml.Signal.t -> pop:Hardcaml.Signal.t ->
  data:Hardcaml.Signal.t -> t

(** [create_with] whose storage word at the write pointer is written in every
    cycle in which the queue is not full (timing knob [fifo_write_free_slot],
    docs/timing-closure.md); [ready], [valid], [level] and the head of a
    non-empty queue are those of [create_with] at every cycle. *)
val create_free_slot : async_reset:Hardcaml.Signal.t option -> storage:storage ->
  clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  width:int -> depth:int -> push:Hardcaml.Signal.t -> pop:Hardcaml.Signal.t ->
  data:Hardcaml.Signal.t -> t

(** [create_free_slot] whose acceptance is gated by [gate] instead of [clear]
    (timing knob [clear_outputs_only]). *)
val create_free_slot_gated : gate:Hardcaml.Signal.t -> async_reset:Hardcaml.Signal.t option ->
  storage:storage -> clock:Hardcaml.Signal.t -> clear:Hardcaml.Signal.t ->
  width:int -> depth:int -> push:Hardcaml.Signal.t -> pop:Hardcaml.Signal.t ->
  data:Hardcaml.Signal.t -> t
