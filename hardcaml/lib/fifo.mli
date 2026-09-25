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
