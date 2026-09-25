(** Complete Tiny Tapeout top generated directly from production Hardcaml. *)
val create : ?debug:bool -> Config.t -> Hardcaml.Circuit.t

(** Fixed physical SRAM refinement, preserving the external ISA2 interface. *)
val create_refinement : ?debug:bool -> Refinement_config.t -> Hardcaml.Circuit.t

(** Test-only synchronous memory model; no production CLI selects this model. *)
val create_refinement_model : ?debug:bool -> Refinement_config.t -> Hardcaml.Circuit.t
