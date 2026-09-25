(** Formal-only wrappers over the unchanged production Engine/SRAM adapter. *)
val adapter : unit -> Hardcaml.Circuit.t
val engine : Refinement_config.t -> Hardcaml.Circuit.t
val processor : Refinement_config.t -> Hardcaml.Circuit.t

(** Fixed two-state variants for OCaml wrapper tests, never CLI-selectable. *)
val adapter_model : unit -> Hardcaml.Circuit.t
val engine_model : Refinement_config.t -> Hardcaml.Circuit.t
